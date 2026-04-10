"""
auth.py - Module d'authentification MSAL pour CopilotScan
==========================================================

Supporte trois modes d'authentification :
  - device-code  : Interactif, pour les administrateurs (Device Code Flow)
  - app-secret   : Non interactif, via Client Secret (pour l'automatisation)
  - app-cert     : Non interactif, via Certificat X.509 (pour l'automatisation sécurisée)

Principe du moindre privilège
------------------------------
Les scopes Microsoft Graph utilisés sont strictement limités au minimum requis
pour l'audit Copilot :

  - Reports.Read.All           : Lecture des rapports d'utilisation Copilot
  - AuditLog.Read.All          : Accès aux journaux d'audit (activité Copilot)
  - Directory.Read.All         : Lecture de l'annuaire (utilisateurs/groupes)
  - User.Read.All              : Informations sur les utilisateurs (licences)

⚠️  Ces permissions APPLICATION nécessitent le consentement d'un Global Admin.
    Le mode device-code utilise des permissions DÉLÉGUÉES (agit au nom de l'admin connecté).

Usage CLI :
    python auth.py --auth device-code
    python auth.py --auth app-secret --config config.yaml
    python auth.py --auth app-cert   --config config.yaml

Variables d'environnement supportées :
    COPILOT_SCAN_CLIENT_ID      : App Registration Client ID
    COPILOT_SCAN_TENANT_ID      : Azure AD Tenant ID
    COPILOT_SCAN_CLIENT_SECRET  : Client Secret (mode app-secret)
    COPILOT_SCAN_CERT_PATH      : Chemin vers le certificat PEM (mode app-cert)
    COPILOT_SCAN_CERT_THUMB     : Thumbprint SHA1 du certificat (optionnel)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import msal
import yaml

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("copilot_scan.auth")


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

GRAPH_BASE_URL: str = "https://graph.microsoft.com"
AUTHORITY_BASE: str = "https://login.microsoftonline.com"

# Scopes DÉLÉGUÉS pour Device Code Flow (agit au nom de l'admin connecté).
# Principe du moindre privilège : seuls les droits nécessaires à l'audit Copilot.
DELEGATED_SCOPES: list[str] = [
    "https://graph.microsoft.com/Reports.Read.All",
    "https://graph.microsoft.com/AuditLog.Read.All",
    "https://graph.microsoft.com/Directory.Read.All",
    "https://graph.microsoft.com/User.Read.All",
]

# Scopes APPLICATION pour Client Credentials (app-secret / app-cert).
# .default demande toutes les permissions APPLICATION accordées dans l'App Registration.
# Limitez les permissions de l'App Registration au strict nécessaire (voir README).
APPLICATION_SCOPES: list[str] = [
    "https://graph.microsoft.com/.default",
]

# Délai maximum (secondes) pour que l'utilisateur complète le Device Code Flow
DEVICE_CODE_TIMEOUT: int = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Modèles de données
# ---------------------------------------------------------------------------


class AuthMode(str, Enum):
    """Modes d'authentification supportés."""

    DEVICE_CODE = "device-code"
    APP_SECRET = "app-secret"
    APP_CERT = "app-cert"


@dataclass
class AuthConfig:
    """
    Configuration d'authentification chargée depuis config.yaml ou variables d'env.

    Attributes:
        client_id: ID de l'App Registration Azure AD.
        tenant_id: ID du tenant Azure AD (ou 'common' pour multi-tenant).
        client_secret: Secret client (mode app-secret uniquement).
        cert_path: Chemin vers la clé privée PEM (mode app-cert uniquement).
        cert_thumbprint: Thumbprint SHA1 du certificat (optionnel, mode app-cert).
        cache_file: Chemin du fichier de cache de tokens (optionnel).
        auth_mode: Mode d'authentification sélectionné.
    """

    client_id: str
    tenant_id: str
    client_secret: str | None = None
    cert_path: Path | None = None
    cert_thumbprint: str | None = None
    cache_file: Path | None = None
    auth_mode: AuthMode = AuthMode.DEVICE_CODE
    extra_scopes: list[str] = field(default_factory=list)

    @property
    def authority(self) -> str:
        """URL de l'autorité Azure AD."""
        return f"{AUTHORITY_BASE}/{self.tenant_id}"

    @property
    def scopes(self) -> list[str]:
        """Retourne les scopes adaptés au mode d'authentification."""
        base = DELEGATED_SCOPES if self.auth_mode == AuthMode.DEVICE_CODE else APPLICATION_SCOPES
        return base + self.extra_scopes


@dataclass
class TokenResult:
    """
    Résultat d'une acquisition de token.

    Attributes:
        access_token: Bearer token à utiliser dans les requêtes Graph.
        expires_in: Durée de validité en secondes.
        token_type: Type de token (généralement 'Bearer').
        scope: Scopes accordés.
        expires_at: Timestamp UNIX d'expiration (calculé).
    """

    access_token: str
    expires_in: int
    token_type: str = "Bearer"
    scope: str = ""
    expires_at: float = field(default_factory=float)

    def __post_init__(self) -> None:
        if not self.expires_at:
            self.expires_at = time.time() + self.expires_in

    @property
    def is_expired(self) -> bool:
        """Retourne True si le token a expiré (avec 60s de marge)."""
        return time.time() >= (self.expires_at - 60)

    @property
    def authorization_header(self) -> str:
        """Header HTTP Authorization prêt à l'emploi."""
        return f"{self.token_type} {self.access_token}"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Erreur générique d'authentification CopilotScan."""


class AuthConfigError(AuthError):
    """Configuration d'authentification invalide ou incomplète."""


class AuthFlowError(AuthError):
    """Échec du flux d'authentification MSAL."""


class TokenExpiredError(AuthError):
    """Le token est expiré et n'a pas pu être renouvelé."""


# ---------------------------------------------------------------------------
# Chargement de la configuration
# ---------------------------------------------------------------------------


def load_config(
    config_path: Path | None = None,
    auth_mode: AuthMode = AuthMode.DEVICE_CODE,
) -> AuthConfig:
    """
    Charge la configuration depuis un fichier YAML et/ou des variables d'environnement.

    Les variables d'environnement ont priorité sur le fichier de configuration.

    Args:
        config_path: Chemin vers config.yaml (optionnel).
        auth_mode: Mode d'authentification à utiliser.

    Returns:
        Instance AuthConfig complète et validée.

    Raises:
        AuthConfigError: Si la configuration est incomplète ou invalide.
    """
    raw: dict = {}

    if config_path and config_path.exists():
        logger.debug("Chargement de la configuration depuis %s", config_path)
        try:
            with config_path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise AuthConfigError(f"Fichier YAML invalide : {config_path}") from exc
    elif config_path:
        logger.warning("Fichier de configuration introuvable : %s", config_path)

    # Variables d'environnement (priorité maximale)
    env_overrides = {
        "client_id": os.getenv("COPILOT_SCAN_CLIENT_ID"),
        "tenant_id": os.getenv("COPILOT_SCAN_TENANT_ID"),
        "client_secret": os.getenv("COPILOT_SCAN_CLIENT_SECRET"),
        "cert_path": os.getenv("COPILOT_SCAN_CERT_PATH"),
        "cert_thumbprint": os.getenv("COPILOT_SCAN_CERT_THUMB"),
    }
    for key, val in env_overrides.items():
        if val is not None:
            raw[key] = val

    # Validation des champs obligatoires
    client_id = raw.get("client_id", "")
    tenant_id = raw.get("tenant_id", "")

    if not client_id:
        raise AuthConfigError(
            "client_id manquant. Définissez COPILOT_SCAN_CLIENT_ID "
            "ou renseignez client_id dans config.yaml."
        )
    if not tenant_id:
        raise AuthConfigError(
            "tenant_id manquant. Définissez COPILOT_SCAN_TENANT_ID "
            "ou renseignez tenant_id dans config.yaml."
        )

    # Résolution du certificat
    cert_path: Path | None = None
    raw_cert = raw.get("cert_path")
    if raw_cert:
        cert_path = Path(raw_cert).expanduser().resolve()
        if not cert_path.exists():
            raise AuthConfigError(f"Certificat introuvable : {cert_path}")

    # Résolution du cache
    cache_file: Path | None = None
    raw_cache = raw.get("cache_file")
    if raw_cache:
        cache_file = Path(raw_cache).expanduser().resolve()

    config = AuthConfig(
        client_id=client_id,
        tenant_id=tenant_id,
        client_secret=raw.get("client_secret"),
        cert_path=cert_path,
        cert_thumbprint=raw.get("cert_thumbprint"),
        cache_file=cache_file,
        auth_mode=auth_mode,
        extra_scopes=raw.get("extra_scopes", []),
    )

    _validate_config_for_mode(config)
    return config


def _validate_config_for_mode(config: AuthConfig) -> None:
    """
    Vérifie que la configuration contient les champs requis pour le mode sélectionné.

    Args:
        config: Configuration à valider.

    Raises:
        AuthConfigError: Si des champs obligatoires sont manquants.
    """
    if config.auth_mode == AuthMode.APP_SECRET and not config.client_secret:
        raise AuthConfigError(
            "Mode app-secret : client_secret requis. "
            "Définissez COPILOT_SCAN_CLIENT_SECRET ou renseignez client_secret dans config.yaml."
        )
    if config.auth_mode == AuthMode.APP_CERT and not config.cert_path:
        raise AuthConfigError(
            "Mode app-cert : cert_path requis. "
            "Définissez COPILOT_SCAN_CERT_PATH ou renseignez cert_path dans config.yaml."
        )


# ---------------------------------------------------------------------------
# Gestionnaire de cache de tokens
# ---------------------------------------------------------------------------


def _build_token_cache(cache_file: Path | None) -> msal.SerializableTokenCache:
    """
    Crée un cache de tokens MSAL, persisté sur disque si cache_file est fourni.

    Args:
        cache_file: Chemin du fichier de cache (None = mémoire uniquement).

    Returns:
        Instance SerializableTokenCache initialisée.
    """
    cache = msal.SerializableTokenCache()

    if cache_file and cache_file.exists():
        logger.debug("Chargement du cache de tokens depuis %s", cache_file)
        cache.deserialize(cache_file.read_text(encoding="utf-8"))

    return cache


def _persist_token_cache(
    cache: msal.SerializableTokenCache,
    cache_file: Path | None,
) -> None:
    """
    Persiste le cache de tokens sur disque si nécessaire.

    Args:
        cache: Cache MSAL à persister.
        cache_file: Chemin de destination (None = pas de persistance).
    """
    if cache_file and cache.has_state_changed:
        logger.debug("Sauvegarde du cache de tokens dans %s", cache_file)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(cache.serialize(), encoding="utf-8")
        # Permissions restrictives sur le fichier de cache
        cache_file.chmod(0o600)


# ---------------------------------------------------------------------------
# Authentification principale
# ---------------------------------------------------------------------------


class CopilotScanAuthenticator:
    """
    Gestionnaire d'authentification MSAL pour CopilotScan.

    Gère l'acquisition, le renouvellement et la mise en cache des tokens
    Microsoft Graph pour les trois modes supportés.

    Example:
        >>> config = load_config(Path("config.yaml"), AuthMode.DEVICE_CODE)
        >>> auth = CopilotScanAuthenticator(config)
        >>> token = auth.acquire_token()
        >>> headers = {"Authorization": token.authorization_header}
    """

    def __init__(self, config: AuthConfig) -> None:
        """
        Initialise l'authentificateur.

        Args:
            config: Configuration d'authentification validée.
        """
        self._config = config
        self._cache = _build_token_cache(config.cache_file)
        self._app: msal.ClientApplication | None = None
        self._current_token: TokenResult | None = None

    def _get_app(self) -> msal.ClientApplication:
        """
        Construit ou retourne l'application MSAL selon le mode d'authentification.

        Returns:
            Instance ConfidentialClientApplication ou PublicClientApplication.
        """
        if self._app is not None:
            return self._app

        cfg = self._config

        if cfg.auth_mode == AuthMode.DEVICE_CODE:
            # PublicClientApplication pour le flux interactif (Device Code)
            self._app = msal.PublicClientApplication(
                client_id=cfg.client_id,
                authority=cfg.authority,
                token_cache=self._cache,
            )
            logger.debug("PublicClientApplication initialisée (Device Code Flow)")

        elif cfg.auth_mode == AuthMode.APP_SECRET:
            # ConfidentialClientApplication avec Client Secret
            self._app = msal.ConfidentialClientApplication(
                client_id=cfg.client_id,
                authority=cfg.authority,
                client_credential=cfg.client_secret,
                token_cache=self._cache,
            )
            logger.debug("ConfidentialClientApplication initialisée (Client Secret)")

        elif cfg.auth_mode == AuthMode.APP_CERT:
            # ConfidentialClientApplication avec Certificat X.509
            cert_pem = cfg.cert_path.read_bytes()  # type: ignore[union-attr]
            credential: dict = {"private_key": cert_pem}
            if cfg.cert_thumbprint:
                credential["thumbprint"] = cfg.cert_thumbprint

            self._app = msal.ConfidentialClientApplication(
                client_id=cfg.client_id,
                authority=cfg.authority,
                client_credential=credential,
                token_cache=self._cache,
            )
            logger.debug("ConfidentialClientApplication initialisée (Certificat)")

        return self._app  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Méthodes d'acquisition de token par mode
    # ------------------------------------------------------------------

    def _acquire_device_code(self) -> TokenResult:
        """
        Lance le flux Device Code Flow (interactif).

        Affiche un code à entrer sur https://microsoft.com/devicelogin.
        L'utilisateur doit être Global Admin ou avoir les droits délégués requis.

        Returns:
            TokenResult avec le token d'accès.

        Raises:
            AuthFlowError: En cas d'échec ou de timeout du flux.
        """
        app = self._get_app()
        assert isinstance(app, msal.PublicClientApplication)

        # Tenter d'abord un token silencieux depuis le cache
        accounts = app.get_accounts()
        if accounts:
            logger.info("Tentative d'acquisition silencieuse (cache)...")
            result = app.acquire_token_silent(
                scopes=self._config.scopes,
                account=accounts[0],
            )
            if result and "access_token" in result:
                logger.info("Token obtenu depuis le cache.")
                return self._parse_token_result(result)

        # Lancement du Device Code Flow
        flow = app.initiate_device_flow(scopes=self._config.scopes)
        if "user_code" not in flow:
            raise AuthFlowError(
                f"Impossible d'initier le Device Code Flow : {flow.get('error_description', 'Erreur inconnue')}"
            )

        # Affichage des instructions pour l'utilisateur
        print("\n" + "=" * 60)
        print("  AUTHENTIFICATION REQUISE")
        print("=" * 60)
        print(
            f"\n  1. Ouvrez : {flow.get('verification_uri', 'https://microsoft.com/devicelogin')}"
        )
        print(f"  2. Entrez le code : {flow['user_code']}")
        print(f"\n  Expiration dans {DEVICE_CODE_TIMEOUT // 60} minutes.")
        print("=" * 60 + "\n")

        logger.info("En attente de l'authentification Device Code...")
        result = app.acquire_token_by_device_flow(
            flow,
            exit_condition=lambda flow: time.time() > flow["expires_at"],
        )

        return self._handle_token_result(result, "Device Code Flow")

    def _acquire_client_credentials(self) -> TokenResult:
        """
        Acquiert un token via Client Credentials (app-secret ou app-cert).

        Tente d'abord une acquisition silencieuse depuis le cache.

        Returns:
            TokenResult avec le token d'accès.

        Raises:
            AuthFlowError: En cas d'échec.
        """
        app = self._get_app()
        assert isinstance(app, msal.ConfidentialClientApplication)

        logger.info("Acquisition du token via Client Credentials...")
        result = app.acquire_token_for_client(scopes=self._config.scopes)
        return self._handle_token_result(result, "Client Credentials")

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    def acquire_token(self) -> TokenResult:
        """
        Acquiert un token d'accès Microsoft Graph selon le mode configuré.

        Si un token valide est en cache, il est retourné directement.
        Sinon, le flux d'authentification approprié est lancé.

        Returns:
            TokenResult prêt à l'emploi.

        Raises:
            AuthFlowError: En cas d'échec du flux d'authentification.
            AuthConfigError: Si la configuration est invalide pour le mode.
        """
        # Retourner le token en mémoire s'il est encore valide
        if self._current_token and not self._current_token.is_expired:
            logger.debug(
                "Token valide en mémoire (expires dans %.0fs).",
                self._current_token.expires_at - time.time(),
            )
            return self._current_token

        logger.info("Acquisition d'un nouveau token (mode: %s)...", self._config.auth_mode.value)

        mode = self._config.auth_mode
        if mode == AuthMode.DEVICE_CODE:
            token = self._acquire_device_code()
        elif mode in (AuthMode.APP_SECRET, AuthMode.APP_CERT):
            token = self._acquire_client_credentials()
        else:
            raise AuthConfigError(f"Mode d'authentification non supporté : {mode}")

        self._current_token = token
        _persist_token_cache(self._cache, self._config.cache_file)

        logger.info(
            "Token acquis avec succès. Expire dans %d secondes.",
            token.expires_in,
        )
        return token

    def refresh_token(self) -> TokenResult:
        """
        Force le renouvellement du token (ignore le cache mémoire).

        Returns:
            Nouveau TokenResult.

        Raises:
            AuthFlowError: En cas d'échec du renouvellement.
        """
        logger.info("Renouvellement forcé du token...")
        self._current_token = None
        return self.acquire_token()

    def get_auth_header(self) -> dict[str, str]:
        """
        Retourne un dictionnaire de headers HTTP avec le token Bearer.

        Renouvelle automatiquement le token s'il est expiré.

        Returns:
            Dictionnaire {"Authorization": "Bearer <token>"}.

        Raises:
            TokenExpiredError: Si le token ne peut pas être renouvelé.
        """
        try:
            token = self.acquire_token()
            if token.is_expired:
                token = self.refresh_token()
        except AuthFlowError as exc:
            raise TokenExpiredError("Impossible de renouveler le token d'accès.") from exc

        return {"Authorization": token.authorization_header}

    # ------------------------------------------------------------------
    # Méthodes utilitaires privées
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_token_result(result: dict) -> TokenResult:
        """
        Convertit un résultat MSAL brut en TokenResult.

        Args:
            result: Dictionnaire retourné par MSAL.

        Returns:
            TokenResult structuré.
        """
        return TokenResult(
            access_token=result["access_token"],
            expires_in=result.get("expires_in", 3600),
            token_type=result.get("token_type", "Bearer"),
            scope=result.get("scope", ""),
        )

    @staticmethod
    def _handle_token_result(result: dict, flow_name: str) -> TokenResult:
        """
        Vérifie le résultat MSAL et lève une exception en cas d'erreur.

        Args:
            result: Résultat brut de l'acquisition MSAL.
            flow_name: Nom du flux pour les messages d'erreur.

        Returns:
            TokenResult si succès.

        Raises:
            AuthFlowError: Si MSAL retourne une erreur.
        """
        if "access_token" not in result:
            error = result.get("error", "unknown_error")
            description = result.get("error_description", "Aucun détail disponible.")
            correlation_id = result.get("correlation_id", "N/A")

            logger.error(
                "[%s] Échec d'authentification : %s — %s (correlation_id: %s)",
                flow_name,
                error,
                description,
                correlation_id,
            )

            # Messages d'erreur spécifiques et actionnables
            user_hint = _get_error_hint(error, description)
            raise AuthFlowError(f"[{flow_name}] {error}: {description}\n💡 {user_hint}")

        return CopilotScanAuthenticator._parse_token_result(result)


# ---------------------------------------------------------------------------
# Aide contextuelle aux erreurs
# ---------------------------------------------------------------------------


def _get_error_hint(error: str, description: str) -> str:
    """
    Retourne un message d'aide contextuel pour les erreurs MSAL courantes.

    Args:
        error: Code d'erreur MSAL/Azure AD.
        description: Description de l'erreur.

    Returns:
        Message d'aide lisible par un humain.
    """
    hints: dict[str, str] = {
        "authorization_pending": "L'utilisateur n'a pas encore complété l'authentification Device Code.",
        "authorization_declined": "L'utilisateur a refusé l'authentification.",
        "expired_token": "Le code Device Code a expiré. Relancez l'authentification.",
        "invalid_client": "Client ID ou secret invalide. Vérifiez votre App Registration.",
        "invalid_grant": "Le token ou le refresh token est invalide. Réauthentifiez-vous.",
        "unauthorized_client": "L'application n'est pas autorisée pour ce flux. Vérifiez les permissions dans Azure AD.",
        "consent_required": "Le consentement administrateur est requis. Un Global Admin doit approuver les permissions.",
        "interaction_required": "Une interaction utilisateur est nécessaire. Utilisez le mode device-code.",
        "AADSTS70011": "Scope invalide. Vérifiez les permissions configurées dans l'App Registration.",
        "AADSTS50020": "L'utilisateur n'appartient pas au tenant configuré.",
        "AADSTS700016": "Application introuvable dans le tenant. Vérifiez le client_id et le tenant_id.",
        "AADSTS65001": "Consentement administrateur requis pour les permissions demandées.",
    }

    # Recherche par code d'erreur exact
    if error in hints:
        return hints[error]

    # Recherche par codes AADSTS dans la description
    for code, hint in hints.items():
        if code.startswith("AADSTS") and code in description:
            return hint

    return (
        "Consultez https://learn.microsoft.com/azure/active-directory/develop/reference-error-codes"
    )


# ---------------------------------------------------------------------------
# Point d'entrée CLI
# ---------------------------------------------------------------------------


def _build_cli_parser() -> argparse.ArgumentParser:
    """Construit le parseur d'arguments CLI."""
    parser = argparse.ArgumentParser(
        prog="auth.py",
        description="Module d'authentification CopilotScan (MSAL Python)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python auth.py --auth device-code
  python auth.py --auth app-secret --config config.yaml
  python auth.py --auth app-cert   --config /etc/copilot-scan/config.yaml

Variables d'environnement :
  COPILOT_SCAN_CLIENT_ID      Client ID de l'App Registration
  COPILOT_SCAN_TENANT_ID      Tenant ID Azure AD
  COPILOT_SCAN_CLIENT_SECRET  Client Secret (mode app-secret)
  COPILOT_SCAN_CERT_PATH      Chemin du certificat PEM (mode app-cert)
  COPILOT_SCAN_CERT_THUMB     Thumbprint SHA1 (optionnel, mode app-cert)
        """,
    )
    parser.add_argument(
        "--auth",
        dest="auth_mode",
        choices=[m.value for m in AuthMode],
        default=AuthMode.DEVICE_CODE.value,
        help="Mode d'authentification (défaut: device-code)",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=Path("config.yaml"),
        help="Chemin vers config.yaml (défaut: ./config.yaml)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Active les logs de debug",
    )
    return parser


def main() -> None:
    """Point d'entrée principal du module d'authentification."""
    parser = _build_cli_parser()
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("msal").setLevel(logging.DEBUG)

    try:
        config = load_config(
            config_path=args.config_path,
            auth_mode=AuthMode(args.auth_mode),
        )

        auth = CopilotScanAuthenticator(config)
        token = auth.acquire_token()

        print("\n✅ Authentification réussie !")
        print(f"   Mode         : {config.auth_mode.value}")
        print(f"   Tenant       : {config.tenant_id}")
        print(f"   Client ID    : {config.client_id}")
        print(f"   Expire dans  : {token.expires_in}s")
        print(f"   Scopes       : {token.scope or ', '.join(config.scopes)}")
        print(f"\n   Token (extrait) : {token.access_token[:40]}…\n")

    except AuthConfigError as exc:
        logger.error("❌ Erreur de configuration : %s", exc)
        sys.exit(1)
    except AuthFlowError as exc:
        logger.error("❌ Erreur d'authentification : %s", exc)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\n⚠️  Authentification annulée.")
        sys.exit(3)


if __name__ == "__main__":
    main()
