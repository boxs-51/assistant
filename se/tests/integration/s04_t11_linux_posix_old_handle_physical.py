"""S04 T11 Linux POSIX old-process handle vs file-mode authority.

TEST ONLY. Synthetic SQLite and disposable UID separation. Does not test
Windows ACL, the historical shipped application, or live account databases.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import pwd
import sqlite3
import sys
import tempfile

import pytest


def _admission():
    if not (sys.platform.startswith('linux') and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('S04_T11_STRICT') == '1'
            and os.environ.get('S04_T11_DISPOSABLE') == '1'):
        pytest.fail('S04_HARNESS_ERROR: strict disposable Linux CI required')
    assert os.geteuid() != pwd.getpwnam('nobody').pw_uid, 'S04_HARNESS_ERROR: controller identity unavailable'


def _db(path: Path):
    con = sqlite3.connect(f'{path.as_uri()}?mode=rw', uri=True, isolation_level=None, timeout=2)
    con.execute('PRAGMA journal_mode=MEMORY')  # avoids journal-file permissions masking existing-FD property
    return con


def _fixture(root: Path, purpose: str):
    assert root.name.startswith('s04t11-')
    root.chmod(0o777)
    db = root / 'authority.sqlite3'
    con = sqlite3.connect(db, isolation_level=None)
    con.execute('PRAGMA journal_mode=MEMORY')
    con.execute('CREATE TABLE pending_registrations(email TEXT PRIMARY KEY, purpose TEXT NOT NULL)')
    email = 's04-t11-' + purpose + '@example.invalid'
    con.execute('INSERT INTO pending_registrations VALUES (?, ?)', (email, purpose))
    con.close()
    db.chmod(0o666)
    return db, email


def _child(payload):
    path=Path(payload['db']).resolve()
    email=payload['email']
    assert path.name == 'authority.sqlite3' and path.parent.name.startswith('s04t11-')
    assert email.endswith('@example.invalid')
    uid=pwd.getpwnam('nobody').pw_uid
    assert os.geteuid()==uid, 'S04_HARNESS_ERROR: subprocess not isolated under nobody'
    action=payload['action']
    if action=='new_write':
        try:
            conn=_db(path)
        except sqlite3.OperationalError:
            return 'DENIED_NEW_OPEN'
        try:
            try:
                conn.execute('DELETE FROM pending_registrations WHERE email=?',(email,))
            except sqlite3.OperationalError as exc:
                if 'readonly' in str(exc).lower() or 'read-only' in str(exc).lower():
                    return 'DENIED_WRITE'
                raise
            return 'UNEXPECTED_WRITE'
        finally:
            conn.close()
    if action=='restore':
        snapshot=Path(payload['snapshot']).resolve()
        assert snapshot.parent==path.parent
        try:
            os.replace(snapshot,path)
        except PermissionError:
            return 'DENIED_FILE_RESTORE'
        return 'UNEXPECTED_FILE_RESTORE'
    if action=='preexisting_handle':
        conn=_db(path)
        try:
            print('READY',flush=True)
            if sys.stdin.readline().strip()!='GO':
                return 'S04_HARNESS_ERROR'
            conn.execute('DROP TRIGGER deny_old_consume')
            conn.execute('DELETE FROM pending_registrations WHERE email=?',(email,))
            return 'EXISTING_HANDLE_BYPASS'
        finally:
            conn.close()
    raise AssertionError('S04_HARNESS_ERROR: invalid child action')


async def _launch(payload, handshake=False):
    exe=sys.executable
    proc=await asyncio.create_subprocess_exec(
        'sudo','-n','-u','nobody','--',exe,str(Path(__file__).resolve()),'--child',
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    assert proc.stdin and proc.stdout
    try:
        proc.stdin.write((json.dumps(payload)+'\n').encode())
        await proc.stdin.drain()
        if handshake:
            line=await asyncio.wait_for(proc.stdout.readline(),8)
            assert line.strip()==b'READY', 'S04_HARNESS_ERROR: old process failed to preopen SQLite FD'
            return proc
        proc.stdin.write(b'GO\n')
        await proc.stdin.drain()
        output=await asyncio.wait_for(proc.stdout.readline(),8)
        await asyncio.wait_for(proc.wait(),8)
        assert proc.returncode==0, 'S04_HARNESS_ERROR: child exit unexpected'
        return output.decode().strip()
    finally:
        if not handshake and proc.returncode is None:
            proc.kill(); await proc.wait()


async def _scenario(purpose,variant):
    _admission()
    with tempfile.TemporaryDirectory(prefix='s04t11-') as tmp:
        root=Path(tmp); db,email=_fixture(root,purpose)
        payload={'db':str(db),'email':email}
        if variant=='new_connection':
            # File read is allowed but old UID is not authorized to create new RW handle.
            db.chmod(0o644);root.chmod(0o755)
            receipt=await _launch({**payload,'action':'new_write'})
            assert receipt in {'DENIED_NEW_OPEN','DENIED_WRITE'},(
                'S04_SECURITY_RED: new old process regained SQLite write authority')
            with sqlite3.connect(db) as con:
                assert con.execute('SELECT count(*) FROM pending_registrations').fetchone()[0]==1
        elif variant=='stale_restore':
            snapshot=root/'old.snapshot.sqlite3'
            snapshot.write_bytes(db.read_bytes())
            snapshot.chmod(0o666)
            db.chmod(0o644);root.chmod(0o755)
            assert await _launch({**payload,'action':'restore','snapshot':str(snapshot)})=='DENIED_FILE_RESTORE'
            assert db.exists() and snapshot.exists()
        elif variant=='preexisting_handle':
            proc=await _launch({**payload,'action':'preexisting_handle'},handshake=True)
            try:
                with _db(db) as controller:
                    controller.execute('''CREATE TRIGGER deny_old_consume BEFORE DELETE
                        ON pending_registrations BEGIN SELECT RAISE(ABORT,'FENCED'); END''')
                db.chmod(0o644);root.chmod(0o755)
                proc.stdin.write(b'GO\n'); await proc.stdin.drain()
                receipt=(await asyncio.wait_for(proc.stdout.readline(),8)).decode().strip()
                await asyncio.wait_for(proc.wait(),8)
                assert proc.returncode==0, 'S04_HARNESS_ERROR: existing handle test failed'
                assert receipt=='EXISTING_HANDLE_BYPASS',(
                    'S04_HARNESS_ERROR: existing FD hazard not reproduced; inspect raw physical')
                with sqlite3.connect(db) as con:
                    assert con.execute('SELECT count(*) FROM pending_registrations').fetchone()[0]==0
                    assert con.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]==0
            finally:
                if proc.returncode is None:
                    proc.kill();await proc.wait()
        else:
            raise AssertionError('S04_HARNESS_ERROR: unexpected test variant')
        root.chmod(0o700);db.chmod(0o600)


@pytest.mark.parametrize('purpose',['registration','password_reset'])
@pytest.mark.parametrize('variant',['new_connection','stale_restore','preexisting_handle'])
def test_t11_posix_legacy_old_uid_file_boundary(purpose,variant):
    asyncio.run(_scenario(purpose,variant))


if __name__=='__main__' and sys.argv[1:]==['--child']:
    try:
        request=json.loads(sys.stdin.readline())
        print(_child(request),flush=True)
    except Exception:
        sys.exit(2)
