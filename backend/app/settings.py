from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dnsadmin_db_path: str = "/data/dnsadmin.db"
    master_encryption_key: str = ""

    dns_backend: str = "knot_k8s"

    namespace: str = "dns-knot"
    knot_configmap_name: str = "knot-config"
    knot_deployment_name: str = "knot"
    knot_axfr_secret_name: str = "knot-axfr"
    knot_axfr_secret_key: str = "axfr.conf"
    default_zone: str = "k3s.local"
    knot_dns_host: str = "knot"
    knot_dns_port: int = 53

    tsig_poll_interval: int = 3600
    zone_versions_limit: int = 100

    jwt_secret: str = ""
    admin_username: str = "admin"
    admin_password: str = "change-me"

    tls_cert_path: str = ""
    tls_key_path: str = ""

    webhook_group_name: str = "dnsadmin.knot.io"

    knot_instances: str = ""

    uvicorn_host: str = "0.0.0.0"
    uvicorn_http_port: int = 8080
    uvicorn_https_port: int = 8443