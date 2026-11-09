"""Create a NEW disposable PostgreSQL 17+ cluster for explicit local acceptance.

Uses an already provisioned runtime; never downloads, reuses another cluster,
installs a service, edits a firewall or enables non-loopback access. Run this QA
helper with a Python containing cryptography. Configuration contains generated
synthetic passwords and must remain in the private .build directory.
"""
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def run(arguments, **kwargs):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError(f"QA utility {Path(arguments[0]).name} failed: {result.stderr[-1500:]}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--port", type=int, default=55488)
    parser.add_argument("--runtime-provenance", help="Recorded local runtime archive provenance JSON")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare files without starting the server")
    parser.add_argument("--bootstrap-directory", help="Finish roles after a prepared owned cluster was started in foreground")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    runtime = Path(args.runtime).resolve()
    binary = runtime / "bin"
    version = run([str(binary / "postgres.exe"), "--version"]).strip()
    if int(version.split()[-1].split(".")[0]) < 17:
        raise RuntimeError("PostgreSQL 17 or newer is required")
    if args.bootstrap_directory:
        directory = Path(args.bootstrap_directory).resolve()
        if directory.parent != (project / ".build").resolve() or not directory.name.startswith("pg-qa-rc4-"):
            raise RuntimeError("Unexpected owned QA directory")
        evidence = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
        settings = json.loads((directory / "qa-config.json").read_text(encoding="utf-8"))
        bootstrap = evidence["bootstrapUser"]
        environment = dict(os.environ, PGPASSFILE=str(directory / "bootstrap.pgpass"), PGSSLMODE="verify-full",
                           PGSSLROOTCERT=settings["owner"]["sslrootcert"], PGSSLCERTMODE="disable",
                           PGREQUIREAUTH="scram-sha-256", PGCHANNELBINDING="require")
        run([str(binary / "psql.exe"), "--host", "127.0.0.1", "--port", str(evidence["port"]), "--username", bootstrap,
             "--dbname", "postgres", "--no-password", "--set", "ON_ERROR_STOP=1", "--file", str(directory / "bootstrap.sql")], env=environment)
        (project / ".build" / "pg-qa-rc4-current.txt").write_text(str(directory), encoding="utf-8")
        print(json.dumps(dict(directory=str(directory), configuration=str(directory / "qa-config.json"), version=version)))
        return
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4)
    directory = project / ".build" / ("pg-qa-rc4-" + suffix)
    directory.mkdir()
    tls = directory / "tls"
    tls.mkdir()
    stamp = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PAK isolated synthetic QA root")])
    ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(stamp - timedelta(minutes=5))
          .not_valid_after(stamp + timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, key_cert_sign=True, crl_sign=True,
                                      content_commitment=False, data_encipherment=False, key_agreement=False,
                                      encipher_only=None, decipher_only=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
            .issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(stamp - timedelta(minutes=5)).not_valid_after(stamp + timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, key_cert_sign=False, crl_sign=False,
                                        content_commitment=False, data_encipherment=False, key_agreement=False,
                                        encipher_only=None, decipher_only=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (tls / "root.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (tls / "server.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (tls / "server.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    bootstrap = "pak_qa_boot_" + secrets.token_hex(4)
    owner, app = "pak_qa_owner_" + secrets.token_hex(4), "pak_qa_app_" + secrets.token_hex(4)
    database = "pak_qa_" + secrets.token_hex(5)
    passwords = {name: secrets.token_urlsafe(32) for name in (bootstrap, owner, app)}
    password_file = directory / "bootstrap-password.txt"
    password_file.write_text(passwords[bootstrap], encoding="utf-8")
    data = directory / "data"
    run([str(binary / "initdb.exe"), "--pgdata", str(data), "--username", bootstrap,
         "--pwfile", str(password_file), "--auth", "scram-sha-256", "--encoding", "UTF8", "--locale", "C"])
    escaped = lambda path: str(path).replace("\\", "/").replace("'", "''")
    (data / "postgresql.conf").write_text("\n".join([
        "listen_addresses='127.0.0.1'", f"port={args.port}", "ssl=on", "ssl_min_protocol_version='TLSv1.2'",
        f"ssl_cert_file='{escaped(tls / 'server.pem')}'", f"ssl_key_file='{escaped(tls / 'server.key')}'",
        "password_encryption='scram-sha-256'", "max_connections=40", "shared_buffers='32MB'",
        "timezone='UTC'", "log_connections=off", "log_statement='none'", "fsync=on", "full_page_writes=on",
    ]) + "\n", encoding="utf-8")
    (data / "pg_hba.conf").write_text("hostssl all all 127.0.0.1/32 scram-sha-256\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n", encoding="utf-8")
    settings = dict(host="127.0.0.1", port=args.port, sslmode="verify-full", sslrootcert=str(tls / "root.pem"),
                    sslcertmode="disable", require_auth="scram-sha-256", channel_binding="require")
    pgpass = directory / "bootstrap.pgpass"
    pgpass.write_text(f"127.0.0.1:{args.port}:*:{bootstrap}:{passwords[bootstrap]}\n", encoding="utf-8")
    environment = dict(os.environ, PGPASSFILE=str(pgpass), PGSSLMODE="verify-full", PGSSLROOTCERT=str(tls / "root.pem"),
                       PGSSLCERTMODE="disable", PGREQUIREAUTH="scram-sha-256", PGCHANNELBINDING="require")
    sql = "\n".join([
        f"CREATE ROLE {owner} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{passwords[owner]}';",
        f"CREATE ROLE {app} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{passwords[app]}';",
        f"CREATE DATABASE {database} OWNER {owner} TEMPLATE template0 ENCODING 'UTF8';",
        f"REVOKE ALL ON DATABASE {database} FROM PUBLIC;", f"GRANT CONNECT ON DATABASE {database} TO {app};",
    ])
    script = directory / "bootstrap.sql"
    script.write_text(sql, encoding="utf-8")
    def config(name):
        return dict(provider="postgresql", database=database, host="127.0.0.1", port=args.port, user=name,
                    password=passwords[name], sslmode="verify-full", sslrootcert=str(tls / "root.pem"),
                    development=True, connect_timeout=5)
    configuration = directory / "qa-config.json"
    configuration.write_text(json.dumps(dict(allowDestructiveTesting=True, owner=config(owner), runtime=config(app)), indent=2), encoding="utf-8")
    evidence = dict(syntheticOnly=True, createdUtc=stamp.isoformat(), host="127.0.0.1", port=args.port, runtime=str(runtime),
                    version=version, provenance="Existing theseus-rs/postgresql_binaries package; no official EDB archive checksum match",
                    configuration=str(configuration), dataDirectory=str(data), bootstrapUser=bootstrap, hashes={})
    for name in ("postgres.exe", "initdb.exe", "pg_ctl.exe", "psql.exe", "pg_dump.exe", "pg_restore.exe"):
        evidence["hashes"][name] = hashlib.sha256((binary / name).read_bytes()).hexdigest()
    if args.runtime_provenance:
        source = Path(args.runtime_provenance).resolve(strict=True)
        recorded = json.loads(source.read_text(encoding="utf-8-sig"))
        if Path(recorded.get("extractionDirectory", "")).resolve() != runtime or recorded.get("zipCrcVerified") is not True:
            raise RuntimeError("Runtime provenance does not match the supplied verified extraction")
        evidence["provenance"] = recorded
    (directory / "provenance.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    (project / ".build" / "pg-qa-rc4-current.txt").write_text(str(directory), encoding="utf-8")
    if not args.prepare_only:
        run([str(binary / "pg_ctl.exe"), "--pgdata", str(data), "--log", str(directory / "postgres.log"), "--wait", "start"])
        run([str(binary / "psql.exe"), "--host", "127.0.0.1", "--port", str(args.port), "--username", bootstrap,
             "--dbname", "postgres", "--no-password", "--set", "ON_ERROR_STOP=1", "--file", str(script)], env=environment)
    print(json.dumps(dict(directory=str(directory), configuration=str(configuration), version=version, port=args.port)))


if __name__ == "__main__":
    main()
