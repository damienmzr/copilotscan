"""
auth.py — CopilotScan v0.1.0
Module d'authentification MSAL pour Microsoft Graph API (Copilot endpoints).

Stratégie :
  - Device Code Flow (DELEGATED) par défaut → requis pour /copilot/admin/catalog/packages
  - Client Credentials Flow (APP-ONLY) disponible mais limité (424 sur /packages/{id})
  - Cache de token persistant (MSAL SerializableTokenCache) pour éviter re-auth

Rôle minimum requis sur le compte connecté : AI Admin + Reports Reader
Scopes couverts :
  - CopilotPackages.Read.All      → GET /catalog/packages, /catalog/packages/{id}
  - Reports.Read.All              → GET /reports/getMicrosoft365CopilotUsageUserDetail
  - AiEnterpriseInteraction.Read.All → GET /copilot/users/{id}/interactionHistory (app-only)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import msal

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("copilotscan.auth")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
GRAPH_BETA_URL = "https://graph.microsoft.com/beta"
AUTHORITY_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}"

# Scopes délégués (Device Code Flow) — principe du moindre privilège
DELEGATED_SCOPES: list[str] = [
    "https://graph.microsoft.com/CopilotPackages.Read.All",
    "https://graph.microsoft.com/Reports.Read.All",
    "offline_access",  # Pour le refresh token
]

# Scopes applicatifs (Client Credentials) — uniquement pour interactionHistory
APP_SCOPES: list[str] = [
    "https://graph.microsoft.com/.default",
]

# Durée minimale de validité du token avant renouvellement anticipé (secondes)
TOKEN_REFRESH_BUFFER_SECONDS = 300  # 5 minutes

# Chemin du cache de tokens par défaut
DEFAULT_CACHE_PATH = Path.home() / ".copilotscan" / "token_cache.bin"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class AuthFlow(str, Enum):
    """Flux d'authentification supportés."""
    DEVICE_CODE = "device_code"       # Délégué, interactif — défaut recommandé
    CLIENT_CREDENTIALS = "client_credentials"  # App-only — limité sur catalog/{id}


# ---------------------------------------------------------------------------
# Dataclasses de configuration
# ---------------------------------------------------------------------------
@dataclass
class AuthConfig:
    """
    Configuration complète pour l'authentification MSAL.

    Exemple d'utilisation minimale (Device Code Flow) :
        config = AuthConfig(
            client_id="<app-registration-client-id>",
            tenant_id="<your-tenant-id>",
        )

    Variables d'environnement supportées (priorité sur les valeurs passées) :
        COPILOTSCAN_CLIENT_ID
        COPILOTSCAN_TENANT_ID
        COPILOTSCAN_CLIENT_SECRET   (uniquement pour client_credentials)
        COPILOTSCAN_CACHE_PATH
    """
    client_id: str = field(default="")
    tenant_id: str = field(default="")
    client_secret: Optional[str] = field(default=None, repr=False)
    flow: AuthFlow = field(default=AuthFlow.DEVICE_CODE)
    cache_path: Path = field(default=DEFAULT_CACHE_PATH)
    # Compte à cibler si plusieurs comptes en cache
    preferred_account: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        # Priorité aux variables d'environnement
        self.client_id = os.getenv("COPILOTSCAN_CLIENT_ID", self.client_id)
        self.tenant_id = os.getenv("COPILOTSCAN_TENANT_ID", self.tenant_id)
        self.client_secret = os.getenv("COPILOTSCAN_CLIENT_SECRET", self.client_secret)
        cache_env = os.getenv("COPILOTSCAN_CACHE_PATH")
        if cache_env:
            self.cache_path = Path(cache_env)

        self._validate()

    def _validate(self) -> None:
        errors: list[str] = []
        if not self.client_id:
            errors.append("client_id manquant (ou COPILOTSCAN_CLIENT_ID non défini)")
        if not self.tenant_id:
            errors.append("tenant_id manquant (ou COPILOTSCAN_TENANT_ID non défini)")
        if self.flow == AuthFlow.CLIENT_CREDENTIALS and not self.client_secret:
            errors.append(
                "client_secret requis pour AuthFlow.CLIENT_CREDENTIALS "
                "(ou COPILOTSCAN_CLIENT_SECRET non défini)\n"
                "⚠️  Rappel : ce flux retourne 424 sur /catalog/packages/{id}"
            )
        if errors:
            raise ValueError(
                "AuthConfig invalide :\n" + "\n".join(f"  • {e}" for e in errors)
            )

    @property
    def authority(self) -> str:
        return AUTHORITY_TEMPLATE.format(tenant_id=self.tenant_id)


# ---------------------------------------------------------------------------
# Résultat d'authentification
# ---------------------------------------------------------------------------
@dataclass
class AuthResult:
    """Token et métadonnées retournés après authentification réussie."""
    access_token: str
    flow_used: AuthFlow
    expires_at: float          # timestamp UTC
    account_upn: Optional[str] = None  # None pour client_credentials
    scopes_granted: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - TOKEN_REFRESH_BUFFER_SECONDS)

    @property
    def bearer_header(self) -> dict[str, str]:
        """Header Authorization prêt à l'emploi pour requests/httpx."""
        return {"Authorization": f"Bearer {self.access_token}"}


# ---------------------------------------------------------------------------
# Gestionnaire de cache de tokens
# ---------------------------------------------------------------------------
class TokenCacheManager:
    """Gère la sérialisation/désérialisation du cache MSAL sur disque."""

    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path
        self._cache = msal.SerializableTokenCache()
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._cache.deserialize(self._path.read_text(encoding="utf-8"))
                logger.debug("Cache de tokens chargé depuis %s", self._path)
            except Exception as exc:
                logger.warning("Cache illisible, réinitialisation : %s", exc)
                self._path.unlink(missing_ok=True)

    def save(self) -> None:
        if self._cache.has_state_changed:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(self._cache.serialize(), encoding="utf-8")
            logger.debug("Cache de tokens sauvegardé dans %s", self._path)

    @property
    def msal_cache(self) -> msal.SerializableTokenCache:
        return self._cache


# ---------------------------------------------------------------------------
# Classe principale
# ---------------------------------------------------------------------------
class CopilotScanAuth:
    """
    Gestionnaire d'authentification MSAL pour CopilotScan.

    Utilisation typique :
        config = AuthConfig(client_id="...", tenant_id="...")
        auth = CopilotScanAuth(config)
        result = auth.authenticate()
        headers = result.bearer_header
    """

    def __init__(self, config: AuthConfig) -> None:
        self._config = config
        self._cache_mgr = TokenCacheManager(config.cache_path)
        self._app: Optional[msal.ClientApplication] = None
        self._last_result: Optional[AuthResult] = None

    # ------------------------------------------------------------------
    # Méthode publique principale
    # ------------------------------------------------------------------
    def authenticate(self) -> AuthResult:
        """
        Authentifie l'application selon le flux configuré.
        Réutilise le token en cache si toujours valide.

        Returns:
            AuthResult avec le token d'accès et les métadonnées.

        Raises:
            AuthenticationError si l'authentification échoue.
        """
        # Réutiliser le token valide en mémoire
        if self._last_result and not self._last_result.is_expired:
            logger.debug("Token en mémoire encore valide, réutilisation.")
            return self._last_result

        logger.info("Démarrage authentification (flux : %s)", self._config.flow.value)

        if self._config.flow == AuthFlow.DEVICE_CODE:
            result = self._device_code_flow()
        elif self._config.flow == AuthFlow.CLIENT_CREDENTIALS:
            result = self._client_credentials_flow()
        else:
            raise ValueError(f"Flux non supporté : {self._config.flow}")

        self._cache_mgr.save()
        self._last_result = result
        logger.info(
            "Authentification réussie | flux=%s | compte=%s | expire dans ~%ds",
            result.flow_used.value,
            result.account_upn or "app-only",
            max(0, int(result.expires_at - time.time())),
        )
        return result

    def get_headers(self) -> dict[str, str]:
        """Raccourci : retourne directement les headers Authorization."""
        return self.authenticate().bearer_header

    def invalidate_cache(self) -> None:
        """Supprime le cache de tokens sur disque et en mémoire."""
        self._last_result = None
        if self._config.cache_path.exists():
            self._config.cache_path.unlink()
            logger.info("Cache de tokens supprimé.")

    # ------------------------------------------------------------------
    # Flux Device Code (DÉLÉGUÉ — défaut recommandé)
    # ------------------------------------------------------------------
    def _device_code_flow(self) -> AuthResult:
        """
        Flux Device Code Flow (RFC 8628).

        Idéal pour :
          - Scripts CLI sans navigateur interactif
          - Comptes avec MFA/Conditional Access
          - Rôle AI Admin + Reports Reader (pas Global Admin)

        Le token est mis en cache — l'utilisateur ne se reconnecte pas
        à chaque exécution tant que le refresh token est valide.
        """
        app = self._get_public_app()

        # 1. Tentative depuis le cache d'abord
        accounts = app.get_accounts(
            username=self._config.preferred_account
        )
        if accounts:
            logger.debug("Compte(s) en cache : %s", [a["username"] for a in accounts])
            token_response = app.acquire_token_silent(
                scopes=DELEGATED_SCOPES,
                account=accounts[0],
            )
            if token_response and "access_token" in token_response:
                logger.info("Token récupéré depuis le cache MSAL.")
                return self._build_result(token_response, AuthFlow.DEVICE_CODE)

        # 2. Initier le Device Code Flow
        flow = app.initiate_device_flow(scopes=DELEGATED_SCOPES)
        if "user_code" not in flow:
            raise AuthenticationError(
                f"Impossible d'initier le Device Code Flow : {flow.get('error_description', flow)}"
            )

        # Affichage clair des instructions
        self._print_device_code_instructions(flow)

        # 3. Attente de la validation utilisateur (polling automatique par MSAL)
        token_response = app.acquire_token_by_device_flow(flow)
        return self._process_token_response(token_response, AuthFlow.DEVICE_CODE)

    # ------------------------------------------------------------------
    # Flux Client Credentials (APP-ONLY — limité)
    # ------------------------------------------------------------------
    def _client_credentials_flow(self) -> AuthResult:
        """
        Flux Client Credentials (OAuth2 client_credentials grant).

        ⚠️  LIMITATIONS IMPORTANTES pour CopilotScan :
          - GET /catalog/packages : fonctionne (LIST uniquement)
          - GET /catalog/packages/{id} : retourne 424 Failed Dependency
          - À n'utiliser que pour AiEnterpriseInteraction.Read.All (interactionHistory)

        Recommandation : préférer Device Code Flow pour les endpoints admin catalog.
        """
        logger.warning(
            "⚠️  Client Credentials Flow actif. "
            "GET /catalog/packages/{id} retournera 424 — utilisez Device Code Flow "
            "pour les endpoints admin catalog."
        )
        app = self._get_confidential_app()
        token_response = app.acquire_token_for_client(scopes=APP_SCOPES)
        return self._process_token_response(token_response, AuthFlow.CLIENT_CREDENTIALS)

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------
    def _get_public_app(self) -> msal.PublicClientApplication:
        """Construit ou réutilise l'app publique MSAL (Device Code)."""
        if not isinstance(self._app, msal.PublicClientApplication):
            self._app = msal.PublicClientApplication(
                client_id=self._config.client_id,
                authority=self._config.authority,
                token_cache=self._cache_mgr.msal_cache,
            )
        return self._app  # type: ignore[return-value]

    def _get_confidential_app(self) -> msal.ConfidentialClientApplication:
        """Construit ou réutilise l'app confidentielle MSAL (Client Credentials)."""
        if not isinstance(self._app, msal.ConfidentialClientApplication):
            self._app = msal.ConfidentialClientApplication(
                client_id=self._config.client_id,
                client_credential=self._config.client_secret,
                authority=self._config.authority,
                token_cache=self._cache_mgr.msal_cache,
            )
        return self._app  # type: ignore[return-value]

    def _process_token_response(
        self, response: dict, flow: AuthFlow
    ) -> AuthResult:
        """Valide la réponse MSAL et lève une erreur si nécessaire."""
        if "access_token" not in response:
            error = response.get("error", "unknown_error")
            description = response.get("error_description", "Aucun détail disponible.")
            raise AuthenticationError(
                f"Échec d'authentification [{flow.value}]\n"
                f"  Erreur : {error}\n"
                f"  Détail : {description}\n\n"
                f"Vérifications :\n"
                f"  • Rôle du compte : AI Admin + Reports Reader requis\n"
                f"  • Consentement admin accordé sur les scopes Graph\n"
                f"  • Tenant ID et Client ID corrects\n"
                f"  • Copilot activé dans le Microsoft 365 Admin Center"
            )
        return self._build_result(response, flow)

    def _build_result(self, response: dict, flow: AuthFlow) -> AuthResult:
        """Construit un AuthResult depuis la réponse MSAL."""
        expires_in = response.get("expires_in", 3600)
        account = response.get("account") or {}
        upn = account.get("username") if account else None
        scopes = response.get("scope", "").split()

        return AuthResult(
            access_token=response["access_token"],
            flow_used=flow,
            expires_at=time.time() + expires_in,
            account_upn=upn,
            scopes_granted=scopes,
        )

    @staticmethod
    def _print_device_code_instructions(flow: dict) -> None:
        """Affiche les instructions Device Code de manière lisible."""
        separator = "─" * 60
        print(f"\n{separator}")
        print("  🔐  CopilotScan — Authentification requise")
        print(separator)
        print(f"  1. Ouvrez : {flow.get('verification_uri', 'https://microsoft.com/devicelogin')}")
        print(f"  2. Entrez le code : {flow.get('user_code', '???')}")
        print(f"  3. Connectez-vous avec un compte ayant le rôle :")
        print(f"       AI Admin  +  Reports Reader")
        print(f"  ℹ️  Code valide {flow.get('expires_in', 900) // 60} minutes")
        print(f"{separator}\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Exception dédiée
# ---------------------------------------------------------------------------
class AuthenticationError(Exception):
    """Levée quand l'authentification MSAL échoue."""


# ---------------------------------------------------------------------------
# Fonction utilitaire de haut niveau
# ---------------------------------------------------------------------------
def get_auth_headers(
    client_id: str,
    tenant_id: str,
    client_secret: Optional[str] = None,
    flow: AuthFlow = AuthFlow.DEVICE_CODE,
    cache_path: Optional[Path] = None,
) -> dict[str, str]:
    """
    Fonction d'entrée simplifiée pour obtenir les headers d'autorisation.

    Args:
        client_id:     ID de l'app registration Azure AD.
        tenant_id:     ID du tenant Microsoft 365.
        client_secret: Secret client (uniquement pour CLIENT_CREDENTIALS).
        flow:          Flux OAuth2 (DEVICE_CODE par défaut — recommandé).
        cache_path:    Chemin du cache de tokens (optionnel).

    Returns:
        Dict {"Authorization": "Bearer <token>"} prêt pour requests/httpx.

    Exemple :
        headers = get_auth_headers(
            client_id="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            tenant_id="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        )
        response = requests.get(
            "https://graph.microsoft.com/beta/copilot/admin/catalog/packages",
            headers=headers,
        )
    """
    kwargs = {}
    if cache_path:
        kwargs["cache_path"] = cache_path

    config = AuthConfig(
        client_id=client_id,
        tenant_id=tenant_id,
        client_secret=client_secret,
        flow=flow,
        **kwargs,
    )
    auth = CopilotScanAuth(config)
    return auth.get_headers()


# ---------------------------------------------------------------------------
# Point d'entrée test rapide
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    """
    Test rapide depuis la ligne de commande.

    Usage :
        python auth.py
        COPILOTSCAN_CLIENT_ID=xxx COPILOTSCAN_TENANT_ID=yyy python auth.py
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    try:
        config = AuthConfig(
            client_id=os.getenv("COPILOTSCAN_CLIENT_ID", ""),
            tenant_id=os.getenv("COPILOTSCAN_TENANT_ID", ""),
            flow=AuthFlow.DEVICE_CODE,
        )
        auth = CopilotScanAuth(config)
        result = auth.authenticate()

        print("\n✅ Authentification réussie !")
        print(f"   Compte    : {result.account_upn or 'app-only'}")
        print(f"   Flux      : {result.flow_used.value}")
        print(f"   Scopes    : {', '.join(result.scopes_granted) or 'N/A'}")
        print(f"   Expire    : dans {max(0, int(result.expires_at - time.time()))}s")
        print(f"   Token     : {result.access_token[:40]}…\n")

    except AuthenticationError as e:
        print(f"\n❌ Erreur d'authentification :\n{e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"\n❌ Configuration invalide :\n{e}", file=sys.stderr)
        sys.exit(2)
