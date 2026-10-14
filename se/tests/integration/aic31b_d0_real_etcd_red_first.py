"""Real etcd v3.7.2 D0 RED-first experiments; NO production admission.
Runs on ephemeral loopback etcd, no repository production configuration.
Every R01-R18 is explicitly accounted for as PASS / RED / HOLD / ERROR.
PASS means only that the scoped experiment passed, NOT D0 security FINAL.
"""
from __future__ import annotations
import base64
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ops.aic31b.barrier import DenyOnlyBarrier, EtcdV3Store, BarrierDenied, Record, certificate_sha256

TARGET = "a" * 64
KEY = "/aic31b/barrier/v1/" + TARGET
OUT = Path("d0_evidence")
OUT.mkdir(exist_ok=True)
RESULTS = {}
EVENTS = []

def emit(message):
    line = datetime.datetime.now(datetime.timezone.utc).isoformat() + " " + str(message)
    EVENTS.append(line)
    print(line, flush=True)

def record(case, status, evidence, **extra):
    assert case not in RESULTS, "duplicate test ID"
    assert status in ("PASS", "RED", "HOLD", "ERROR")
    RESULTS[case] = {"status": status, "evidence": str(evidence), **extra}
    emit(case + " " + status + " " + str(evidence))
    persist()

def persist():
    (OUT / "results.json").write_text(json.dumps({
        "source_head": os.environ.get("GITHUB_SHA", "unverified-local"),
        "runner_os": sys.platform,
        "etcd_version": "v3.7.2",
        "cases": RESULTS,
    }, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "raw_tests.txt").write_text("\n".join(EVENTS) + "\n", encoding="utf-8")

def attempt(case, fn):
    try:
        state, msg = fn()
        record(case, state, msg)
    except Exception as exc:
        record(case, "ERROR", type(exc).__name__ + ": " + str(exc))
        emit(traceback.format_exc())

def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

def make_pki(directory):
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    cakey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    caname = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AIC31B disposable CA")])
    ca = (x509.CertificateBuilder().subject_name(caname).issuer_name(caname)
          .public_key(cakey.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=3))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(cakey, hashes.SHA256()))
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for name in ("server", "operator", "reviewer", "outsider"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert_builder = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(caname).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=2)))
        if name == "server":
            cert_builder = cert_builder.add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]), critical=False).add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        else:
            cert_builder = cert_builder.add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        cert = cert_builder.sign(cakey, hashes.SHA256())
        (directory / (name + ".pem")).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (directory / (name + ".key")).write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
    return directory

class Cluster:
    def __init__(self, root):
        self.root = root
        self.certdir = make_pki(root / "certs")
        self.port = free_port()
        self.peer = free_port()
        self.endpoint = "https://127.0.0.1:" + str(self.port)
        ext = ".exe" if os.name == "nt" else ""
        bins = Path(os.environ["ETCD_BIN_DIR"]).resolve()
        self.etcd = bins / ("etcd" + ext)
        self.ctl = bins / ("etcdctl" + ext)
        self.utl = bins / ("etcdutl" + ext)
        for exe in (self.etcd, self.ctl, self.utl):
            if not exe.exists():
                raise RuntimeError("missing real binary: " + str(exe))
        self.proc = None
        self.logfile = None
        self.data = root / "first"
        self.client_cert_auth = False  # Phase A: REST epoch/restore negative test; no RBAC claim.
    def start(self, data=None):
        self.stop()
        self.data = data or self.data
        self.data.mkdir(parents=True, exist_ok=True)
        self.logfile = open(OUT / "etcd-server.log", "a", encoding="utf-8")
        args = [str(self.etcd), "--name=node", "--data-dir=" + str(self.data),
                "--listen-client-urls=" + self.endpoint,
                "--advertise-client-urls=" + self.endpoint,
                "--listen-peer-urls=http://127.0.0.1:" + str(self.peer),
                "--initial-advertise-peer-urls=http://127.0.0.1:" + str(self.peer),
                "--initial-cluster=node=http://127.0.0.1:" + str(self.peer),
                "--initial-cluster-state=new",
                "--cert-file=" + str(self.certdir / "server.pem"),
                "--key-file=" + str(self.certdir / "server.key"),
                "--trusted-ca-file=" + str(self.certdir / "ca.pem"),
                "--client-cert-auth=" + ("true" if self.client_cert_auth else "false")]
        self.proc = subprocess.Popen(args, stdout=self.logfile, stderr=subprocess.STDOUT)
        last = None
        for _ in range(45):
            if self.proc.poll() is not None:
                raise RuntimeError("etcd exited early " + str(self.proc.returncode))
            try:
                self.cli("endpoint", "health")
                emit("real etcd endpoint healthy; pid=" + str(self.proc.pid))
                return
            except Exception as exc:
                last = exc
                time.sleep(0.25)
        raise RuntimeError("etcd startup/health failed: " + str(last))
    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=8)
            self.proc = None
        if self.logfile is not None:
            self.logfile.close()
            self.logfile = None
    def hardkill(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=8)
            emit("OS hard-kill etcd process acknowledged")
        if self.logfile is not None:
            self.logfile.close()
            self.logfile = None
        self.proc = None
    def cli(self, *argv, identity="operator", root=False, check=True):
        cmd = [str(self.ctl), "--endpoints=" + self.endpoint,
               "--cacert=" + str(self.certdir / "ca.pem"),
               "--cert=" + str(self.certdir / (identity + ".pem")),
               "--key=" + str(self.certdir / (identity + ".key")),
               "--command-timeout=4s"]
        if root:
            cmd += ["--user=root:DisposableOnly-Pw14"]
        cmd += [str(v) for v in argv]
        run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, timeout=15)
        if check and run.returncode != 0:
            raise RuntimeError("etcdctl command failed [" + str(argv[0]) +
                               "] rc=" + str(run.returncode) +
                               " stderr=" + run.stderr[:240])
        return run
    def store(self):
        return EtcdV3Store(endpoint=self.endpoint, ca=str(self.certdir / "ca.pem"),
                cert=str(self.certdir / "operator.pem"),
                key=str(self.certdir / "operator.key"))
    def barrier(self, actor="operator", operator="operator"):
        return DenyOnlyBarrier(
             store=self.store(), target_sha256=TARGET,
             actor_cert_sha256=certificate_sha256(str(self.certdir / (actor + ".pem"))),
             operator_cert_sha256=certificate_sha256(str(self.certdir / (operator + ".pem"))),
             reviewer_cert_sha256=certificate_sha256(str(self.certdir / "reviewer.pem")))
    def snapshot(self, dest):
        self.cli("snapshot", "save", str(dest))
        if not dest.exists() or dest.stat().st_size < 4096:
            raise RuntimeError("real snapshot missing or too small")
    def restore(self, snapshot, dest):
        self.stop()
        cmd = [str(self.utl), "snapshot", "restore", str(snapshot),
               "--data-dir=" + str(dest), "--name=node",
               "--initial-cluster=node=http://127.0.0.1:" + str(self.peer),
               "--initial-advertise-peer-urls=http://127.0.0.1:" + str(self.peer),
               "--initial-cluster-token=d0-restored"]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
        if run.returncode:
            raise RuntimeError("real etcdutl snapshot restore rc=" + str(run.returncode) +
                               " " + run.stderr[-250:])
        emit("real etcdutl snapshot restore completed; bytes=" + str(snapshot.stat().st_size))
        self.start(dest)

def main():
    emit("D0 REAL-ETCD RED FIRST; S0 NO OPEN; no production endpoints")
    with tempfile.TemporaryDirectory(prefix="aic381_d0_") as scratch:
        c = Cluster(Path(scratch))
        c.start()
        try:
            # R01: demonstrate real key deletion resets application epoch to one.
            def r01():
                initial = c.barrier().seal(expected_epoch=0, ticket="R01A")
                assert initial.epoch == 1
                deleted = c.cli("del", KEY)
                assert deleted.stdout.strip().endswith("1")
                replay = c.barrier().seal(expected_epoch=0, ticket="R01REPLAY")
                return ("RED" if replay.epoch == 1 else "PASS",
                        "real etcd DELETE; old E=1, reissued E=" + str(replay.epoch))
            attempt("R01", r01)

            # R02: real etcdctl snapshot + real etcdutl restore to new data directory.
            def r02():
                c.cli("del", KEY)
                assert c.store().cas(TARGET, 0, Record(TARGET, 7, "OPEN", "R02A", certificate_sha256(str(c.certdir / "operator.pem"))))
                snapshot = Path(scratch) / "before-epoch8.db"
                c.snapshot(snapshot)
                assert c.barrier().seal(expected_epoch=7, ticket="R02NEW").epoch == 8
                c.restore(snapshot, Path(scratch) / "restored")
                replay = c.barrier().seal(expected_epoch=7, ticket="R02REPLAY")
                return ("RED" if replay.epoch == 8 else "PASS",
                        "real snapshot restore; prior E=8 and replay issued E=" + str(replay.epoch))
            attempt("R02", r02)

            def r03():
                c.cli("del", KEY)
                received = c.barrier(actor="outsider", operator="outsider").seal(
                     expected_epoch=0, ticket="R03UNTRUSTED")
                return ("RED", "client-self-designated operator SHA256 accepted by S0, E=" +
                        str(received.epoch)) if received.epoch else ("PASS", "denied")
            attempt("R03", r03)

            # Full server-side MOD/VERSION compare, two distinct controllers.
            def do_race(label):
                c.cli("del", KEY)
                s = c.store()
                _, mod = s.read(TARGET)
                assert mod == 0
                synchronizer = threading.Barrier(2)
                replies = []
                def worker(ticket):
                    synchronizer.wait(timeout=8)
                    rec = Record(TARGET, 1, "SEAL_REQUESTED", ticket,
                                 certificate_sha256(str(c.certdir / "operator.pem")))
                    replies.append(s.cas(TARGET, 0, rec))
                a = threading.Thread(target=worker, args=(label + "A",))
                b = threading.Thread(target=worker, args=(label + "B",))
                a.start(); b.start(); a.join(timeout=15); b.join(timeout=15)
                if a.is_alive() or b.is_alive():
                    raise RuntimeError("race worker did not terminate")
                return replies.count(True), replies.count(False)
            attempt("R04", lambda: ("PASS" if do_race("R04") == (1,1) else "RED", "real server transaction single winner"))
            record("R05", "HOLD", "PR519 has no external rollback-resistant witness W; cannot test W failure as successful denial")

            # R12 duplicate logical intent idempotent under normal etcd.
            def r12():
                c.cli("del", KEY)
                first = c.barrier().seal(expected_epoch=0, ticket="R12SAME")
                repeat = c.barrier().seal(expected_epoch=1, ticket="R12SAME")
                return ("PASS" if first == repeat else "RED", "real etcd same-ticket repeat did not change state")
            attempt("R12", r12)
            attempt("R13", lambda: ("PASS" if do_race("R13") == (1,1) else "RED", "real etcd opposing requests, loser CAS refused"))
            record("R14", "HOLD", "no W reservation API exists; pre-reservation hard-crash has no durable D0 proof")

            def r15():
                c.cli("del", KEY)
                snippet = ("import os; from ops.aic31b.barrier import DenyOnlyBarrier,EtcdV3Store,certificate_sha256; "
                  "from pathlib import Path; c=Path(" + repr(str(c.certdir)) + "); "
                  "f=lambda n: certificate_sha256(str(c/(n+'.pem'))); "
                  "s=EtcdV3Store(endpoint=" + repr(c.endpoint) +
                  ",ca=str(c/'ca.pem'),cert=str(c/'operator.pem'),key=str(c/'operator.key')); "
                  "b=DenyOnlyBarrier(store=s,target_sha256=" + repr(TARGET) +
                  ",actor_cert_sha256=f('operator'),operator_cert_sha256=f('operator'),reviewer_cert_sha256=f('reviewer')); "
                  "b.seal(expected_epoch=0,ticket='R15WRITE'); os._exit(73)")
                died = subprocess.run([sys.executable, "-c", snippet], timeout=20)
                read = c.store().read(TARGET)[0]
                assert died.returncode == 73 and read and read.ticket == "R15WRITE"
                return "HOLD", "real child os._exit(73) after etcd CAS; receipt survived, but no external W/ACK recovery"
            attempt("R15", r15)

            def r16():
                c.cli("del", KEY)
                operator = certificate_sha256(str(c.certdir / "operator.pem"))
                assert c.store().cas(TARGET, 0, Record(TARGET, 55, "OPEN", "R16INJECT", operator))
                try:
                    c.barrier().require_admitted()
                    return "RED", "injected OPEN admitted"
                except BarrierDenied:
                    return "PASS", "real etcd injected OPEN never grants S0 admission"
            attempt("R16", r16)
            record("R17", "HOLD", "no signed physical DB target binding/cluster incarnation verifier exists in PR519")
            def r18():
                before = c.store().read(TARGET)[0]
                c.hardkill()
                c.start()
                after = c.store().read(TARGET)[0]
                assert before == after
                return "HOLD", "real etcd OS hard kill/restart preserves local receipt; independent W/quorum partition unimplemented"
            attempt("R18", r18)

            # Activate actual RBAC: new server process with TLS client CN enforcement.
            c.client_cert_auth = True
            c.stop()
            c.start(c.data)
            def auth_setup():
                c.cli("role", "add", "root")
                c.cli("user", "add", "root:DisposableOnly-Pw14")
                c.cli("user", "grant-role", "root", "root")
                c.cli("role", "add", "operator")
                c.cli("role", "grant-permission", "operator", "readwrite", KEY)
                c.cli("user", "add", "operator", "--no-password")
                c.cli("user", "grant-role", "operator", "operator")
                c.cli("role", "add", "reviewer")
                c.cli("role", "grant-permission", "reviewer", "read", KEY)
                c.cli("user", "add", "reviewer", "--no-password")
                c.cli("user", "grant-role", "reviewer", "reviewer")
                c.cli("user", "add", "outsider", "--no-password")
                c.cli("auth", "enable")
                c.cli("get", KEY, identity="operator")
                emit("native gRPC CN operator authenticated after enabling etcd RBAC")
            auth_setup()

            def r06():
                # Native gRPC CN succeeds, but HTTP gRPC-gateway has no CN actor propagation.
                c.cli("get", KEY, identity="operator")
                ctx = ssl.create_default_context(cafile=str(c.certdir / "ca.pem"))
                ctx.load_cert_chain(str(c.certdir / "operator.pem"), str(c.certdir / "operator.key"))
                req = urllib.request.Request(c.endpoint + "/v3/kv/range",
                   data=json.dumps({"key": base64.b64encode(KEY.encode()).decode()}).encode(),
                   headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urllib.request.urlopen(req, context=ctx, timeout=6) as resp:
                        response = resp.read().decode()
                    obj = json.loads(response)
                    if "kvs" in obj:
                        return "RED", "REST returned KV despite missing CN mapping; investigate authorization"
                    return "RED", "native CN usable, REST gateway failed authorized read: " + response[:140]
                except (urllib.error.HTTPError, urllib.error.URLError) as err:
                    return "RED", "native CN get succeeds; REST gateway CN authentication unavailable: " + type(err).__name__
            attempt("R06", r06)

            def r07():
                bad = c.cli("get", KEY, identity="outsider", check=False)
                return ("PASS" if bad.returncode else "RED",
                        "real RBAC outsiders get rc=" + str(bad.returncode))
            attempt("R07", r07)
            record("R08", "RED", "actual auth-off pre-RBAC client self-designation accepted in R03; client-supplied fingerprint is not server-authenticated")
            def r09():
                # Direct operator KV write is permitted under this deliberately permissive test ACL.
                write = c.cli("put", KEY, "R09UNAUTHORIZEDDIRECT", identity="operator", check=False)
                delete = c.cli("del", KEY, identity="operator", check=False)
                return ("RED" if write.returncode == 0 and delete.returncode == 0 else "PASS",
                        "real direct operator PUT rc=" + str(write.returncode) +
                        " DELETE rc=" + str(delete.returncode) + "; broker-only ACL required")
            attempt("R09", r09)
            def r10():
                c.cli("user", "revoke-role", "operator", "operator", root=True)
                denied = c.cli("put", KEY, "R10OLDROLE", identity="operator", check=False)
                return ("HOLD" if denied.returncode else "RED",
                        "live RBAC grant revoked; NEW gRPC connection rc=" + str(denied.returncode) +
                        "; active TLS channel/cert revocation not verified")
            attempt("R10", r10)
            def r11():
                rd = c.cli("get", KEY, identity="reviewer", check=False)
                wr = c.cli("put", KEY, "R11BAD", identity="reviewer", check=False)
                return ("PASS" if rd.returncode == 0 and wr.returncode != 0 else "RED",
                        "real reviewer read rc=" + str(rd.returncode) + " write rc=" + str(wr.returncode))
            attempt("R11", r11)

        finally:
            c.stop()

    for num in range(1,19):
        case = "R" + str(num).zfill(2)
        if case not in RESULTS:
            record(case, "ERROR", "not reached: test prerequisite or infrastructure failure")
    persist()
    emit("D0 summary " + json.dumps({k: sum(r["status"] == k for r in RESULTS.values())
                                     for k in ("PASS","RED","HOLD","ERROR")}))
    persist()
    return 1 if any(r["status"] != "PASS" for r in RESULTS.values()) else 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        emit("FATAL real etcd fixture: " + repr(exc))
        emit(traceback.format_exc())
        for num in range(1,19):
            key = "R" + str(num).zfill(2)
            if key not in RESULTS:
                record(key, "ERROR", "infrastructure setup failed: " + repr(exc))
        persist()
        sys.exit(2)
