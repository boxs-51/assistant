"""S04 T12 Windows NTFS ACL and pre-opened SQLite handle: physical reference.

Test-only on disposable, Microsoft-hosted Windows Actions runner. The
controller creates and deletes ONE random unprivileged local user, passes its
password only to CreateProcessWithLogonW (never CLI/log), and uses only files
under a randomized temporary directory. Not the old shipped app binary.
"""
from __future__ import annotations

import ctypes
from contextlib import closing
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import pytest


class USER_INFO_1(ctypes.Structure):
    _fields_=[
        ("name",wintypes.LPWSTR),("password",wintypes.LPWSTR),
        ("password_age",wintypes.DWORD),("priv",wintypes.DWORD),
        ("home_dir",wintypes.LPWSTR),("comment",wintypes.LPWSTR),
        ("flags",wintypes.DWORD),("script_path",wintypes.LPWSTR)]


class STARTUPINFO(ctypes.Structure):
    _fields_=[
        ("cb",wintypes.DWORD),("lpReserved",wintypes.LPWSTR),
        ("lpDesktop",wintypes.LPWSTR),("lpTitle",wintypes.LPWSTR),
        ("dwX",wintypes.DWORD),("dwY",wintypes.DWORD),
        ("dwXSize",wintypes.DWORD),("dwYSize",wintypes.DWORD),
        ("dwXCountChars",wintypes.DWORD),("dwYCountChars",wintypes.DWORD),
        ("dwFillAttribute",wintypes.DWORD),("dwFlags",wintypes.DWORD),
        ("wShowWindow",wintypes.WORD),("cbReserved2",wintypes.WORD),
        ("lpReserved2",ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput",wintypes.HANDLE),("hStdOutput",wintypes.HANDLE),
        ("hStdError",wintypes.HANDLE)]


class PROCESS_INFO(ctypes.Structure):
    _fields_=[("hProcess",wintypes.HANDLE),("hThread",wintypes.HANDLE),
              ("pid",wintypes.DWORD),("tid",wintypes.DWORD)]


def _admit():
    if not (sys.platform=="win32"
            and os.environ.get("GITHUB_ACTIONS")=="true"
            and os.environ.get("S04_T12_DISPOSABLE")=="1"
            and os.environ.get("S04_T12_STRICT")=="1"
            and os.environ.get("RUNNER_ENVIRONMENT")=="github-hosted"
            and os.environ.get("RUNNER_TEMP")):
        pytest.fail("S04_HARNESS_ERROR: hosted disposable Windows runner required")


def _account(name,password,add):
    api=ctypes.WinDLL("Netapi32",use_last_error=True)
    if add:
        f=api.NetUserAdd
        f.argtypes=(wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_void_p,
                    ctypes.POINTER(wintypes.DWORD))
        f.restype=wintypes.DWORD
        u=USER_INFO_1(name,password,0,1,None,"S04 temporary CI fixture",1,None)
        error=wintypes.DWORD()
        rc=f(None,1,ctypes.byref(u),ctypes.byref(error))
    else:
        f=api.NetUserDel
        f.argtypes=(wintypes.LPCWSTR,wintypes.LPCWSTR)
        f.restype=wintypes.DWORD
        rc=f(None,name)
    assert rc==0, f"S04_HARNESS_ERROR: disposable account {'create' if add else 'delete'} status {rc}"


def _icacls(path,*args):
    assert Path(path).exists() and "s04t12-" in str(Path(path))
    p=subprocess.run(["icacls",str(path),*args],capture_output=True,text=True,timeout=12)
    assert p.returncode==0, "S04_HARNESS_ERROR: disposable NTFS ACL operation failed"


def _db(file:Path):
    con=sqlite3.connect(file,isolation_level=None,timeout=2)
    con.execute("PRAGMA journal_mode=MEMORY")
    return con


def _worker(req):
    assert req["db"].endswith("authority.sqlite3")
    assert Path(req["db"]).parent.name=="db"
    assert "s04t12-" in req["db"]
    actor=ctypes.create_unicode_buffer(260)
    size=wintypes.DWORD(260)
    ok=ctypes.WinDLL("advapi32",use_last_error=True).GetUserNameW(actor,ctypes.byref(size))
    assert ok and actor.value.lower()==req["username"].lower(), (
        "S04_HARNESS_ERROR: child token identity not distinct")
    action=req["action"]; email=req["email"]; db=Path(req["db"])
    assert email.endswith("@example.invalid")
    if action=="new_write":
        try:
            con=_db(db)
        except sqlite3.OperationalError:
            return "NEW_OPEN_DENIED"
        try:
            try:
                con.execute("DELETE FROM pending_registrations WHERE email=?",(email,))
            except sqlite3.OperationalError as exc:
                assert ("readonly" in str(exc).lower() or
                        "access" in str(exc).lower() or
                        "unable to open" in str(exc).lower()), (
                    "S04_HARNESS_ERROR: unexpected SQLite error")
                return "NEW_WRITE_DENIED"
            return "UNEXPECTED_WRITE"
        finally:
            con.close()
    if action=="restore":
        try:
            os.replace(req["snapshot"],req["db"])
        except OSError as exc:
            assert getattr(exc,"winerror",None) in (5,32), (
                "S04_HARNESS_ERROR: unexpected NTFS file replacement error")
            return "RESTORE_DENIED"
        return "UNEXPECTED_RESTORE"
    if action=="held_handle":
        c=_db(db)
        try:
            Path(req["status"]).write_text("READY",encoding="ascii")
            for _ in range(300):
                if Path(req["go"]).exists():break
                time.sleep(.05)
            else:
                return "S04_HARNESS_ERROR_TIMEOUT"
            c.execute("DROP TRIGGER deny_old_consume")
            c.execute("DELETE FROM pending_registrations WHERE email=?",(email,))
            return "HELD_HANDLE_BYPASS"
        finally:
            c.close()
    raise AssertionError("S04_HARNESS_ERROR: invalid operation")


def _child_main():
    # No trusted paths or external credentials, only fixture-only JSON and
    # a single short receipt. Source/config/status live in the disposable root.
    req=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    try:
        response=_worker(req)
    except Exception as exc:
        response="S04_CHILD_ERROR_"+type(exc).__name__
    Path(req["status"]).write_text(response,encoding="ascii")
    sys.exit(0 if not response.startswith("S04_CHILD_ERROR_") else 2)


def _spawn(name,password,script,request,root):
    adv=ctypes.WinDLL("advapi32",use_last_error=True)
    fn=adv.CreateProcessWithLogonW
    fn.argtypes=(wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.LPCWSTR,
       wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPWSTR,wintypes.DWORD,
       ctypes.c_void_p,wintypes.LPCWSTR,ctypes.POINTER(STARTUPINFO),
       ctypes.POINTER(PROCESS_INFO))
    fn.restype=wintypes.BOOL
    cmd=ctypes.create_unicode_buffer(subprocess.list2cmdline(
        [sys.executable,str(script),"--child",str(request)]))
    startup=STARTUPINFO()
    startup.cb=ctypes.sizeof(startup)
    proc=PROCESS_INFO()
    ok=fn(name,os.environ["COMPUTERNAME"],password,0,sys.executable,cmd,
          0x08000000,None,str(root),ctypes.byref(startup),ctypes.byref(proc))
    assert ok, ("S04_HARNESS_ERROR: CreateProcessWithLogonW Win32 code "
                + str(ctypes.get_last_error()))
    ctypes.windll.kernel32.CloseHandle(proc.hThread)
    assert proc.pid != os.getpid(), "S04_HARNESS_ERROR: nonseparate process"
    return proc.hProcess


def _wait(handle,timeout_ms=25000):
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    r=k.WaitForSingleObject(handle,timeout_ms)
    if r==0x102:
        k.TerminateProcess(handle,99)
        k.WaitForSingleObject(handle,5000)
        pytest.fail("S04_HARNESS_ERROR: old process did not exit")
    assert r==0, "S04_HARNESS_ERROR: old process wait failed"
    code=wintypes.DWORD()
    assert k.GetExitCodeProcess(handle,ctypes.byref(code))
    assert code.value==0, "S04_HARNESS_ERROR: old process failed ("+str(code.value)+")"


def _poll_ready(path,handle):
    k=ctypes.WinDLL("kernel32",use_last_error=True)
    for _ in range(240):
        if path.exists():
            value=path.read_text(encoding="ascii")
            if value=="READY":return
            pytest.fail("S04_HARNESS_ERROR: early child receipt "+value[:48])
        if k.WaitForSingleObject(handle,0)==0:
            pytest.fail("S04_HARNESS_ERROR: child exited without READY")
        time.sleep(.05)
    pytest.fail("S04_HARNESS_ERROR: missing pre-open READY receipt")


def _scenario(purpose,action):
    _admit()
    name="s4w"+uuid4().hex[:10]
    password="Aa9!"+secrets.token_urlsafe(24)
    created=False
    handle=None
    try:
        _account(name,password,True)
        created=True
        # All test artifacts are in a unique directory on the hosted runner.
        with tempfile.TemporaryDirectory(prefix="s04t12-",dir=os.environ["RUNNER_TEMP"]) as td:
            root=Path(td)
            dbdir=root/"db";outdir=root/"out"
            dbdir.mkdir();outdir.mkdir()
            db=dbdir/"authority.sqlite3"
            email="t12-"+purpose+"@example.invalid"
            with closing(_db(db)) as c:
                c.execute("CREATE TABLE pending_registrations(email TEXT PRIMARY KEY)")
                c.execute("INSERT INTO pending_registrations VALUES(?)",(email,))
            who=os.environ["COMPUTERNAME"]+"\\"+name
            _icacls(root,"/grant:r",f"{who}:(RX)")
            _icacls(dbdir,"/grant:r",f"{who}:(M)")
            _icacls(db,"/grant:r",f"{who}:(M)")
            _icacls(outdir,"/grant:r",f"{who}:(OI)(CI)M")
            script=outdir/"old-child.py";script.write_bytes(Path(__file__).read_bytes())
            config=outdir/"request.json";status=outdir/"result.txt"
            go=outdir/"go.signal"
            snapshot=outdir/"old.snapshot.sqlite3";snapshot.write_bytes(db.read_bytes())
            request={"db":str(db),"email":email,"username":name,"action":action,
                     "status":str(status),"go":str(go),"snapshot":str(snapshot)}
            config.write_text(json.dumps(request),encoding="utf-8")
            if action=="held_handle":
                # NEGATIVE: process MUST hold a writable SQLite handle
                # before the controller changes NTFS permissions.
                handle=_spawn(name,password,script,config,root)
                _poll_ready(status,handle)
                with closing(_db(db)) as controller:
                    controller.execute("""CREATE TRIGGER deny_old_consume BEFORE DELETE
                        ON pending_registrations BEGIN SELECT RAISE(ABORT,'FENCED'); END""")
                status.unlink()
            # The ACL cutover happens before all NEW process launches.
            _icacls(db,"/deny",f"{who}:(W)")
            _icacls(dbdir,"/deny",f"{who}:(WD,AD,DC,D)")
            if action=="held_handle":
                go.write_text("GO",encoding="ascii")
            else:
                # This must not race with the ACL change: a pre-cutover
                # process that writes successfully is not a false GREEN.
                handle=_spawn(name,password,script,config,root)
            _wait(handle)
            receipt=status.read_text(encoding="ascii")
            if action=="new_write":
                assert receipt in ("NEW_OPEN_DENIED","NEW_WRITE_DENIED"), (
                    "S04_SECURITY_RED: new old NTFS writer bypassed ACL")
            elif action=="restore":
                assert receipt=="RESTORE_DENIED", (
                    "S04_SECURITY_RED: old identity restored stale SQLite file")
                assert snapshot.exists()
            else:
                assert receipt=="HELD_HANDLE_BYPASS", (
                    "S04_HARNESS_ERROR: old writable NTFS handle negative control missing")
            with closing(_db(db)) as c:
                count=c.execute("SELECT count(*) FROM pending_registrations").fetchone()[0]
                if action=="held_handle":
                    assert count==0
                    assert c.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]==0
                else:
                    assert count==1
    finally:
        if handle:
            ctypes.WinDLL("kernel32",use_last_error=True).CloseHandle(handle)
        if created:
            _account(name,password,False)


@pytest.mark.parametrize("purpose",["registration","password_reset"])
@pytest.mark.parametrize("action",["new_write","restore","held_handle"])
def test_t12_ntfs_old_user_disposable_file_boundary(purpose,action):
    _scenario(purpose,action)


if __name__=="__main__" and sys.argv[1:2]==["--child"]:
    _child_main()
