# VM Setup / Reconnection Notes (for Claude)

This project's benchmarking work (perf profiling, running pyperformance,
building/testing optimizations) happens inside a QEMU/KVM VM, not on the
host directly. This file has everything a fresh Claude session needs to
reconnect to that VM without re-deriving it from scratch.

## VM Details

- Host machine: `/csl/ece882-040` (a shared lab machine — other users have
  their own unrelated QEMU VMs running there too; only touch a VM whose
  disk file matches the one below).
- Disk image: `/csl/ece882-040/jammy-server-cloudimg-amd64-disk-kvm.img`
  (Ubuntu 22.04 jammy cloud image).
- Login inside the VM: `root` / `ubuntu`.

## Step 1 — Check if it's already running

```
ps aux | grep qemu-system-x86_64
```

Look for a process with `-drive file=jammy-server-cloudimg-amd64-disk-kvm.img`
launched from `/csl/ece882-040` (check its cwd via
`readlink -f /proc/<pid>/cwd`). If found, skip to Step 3.

## Step 2 — If it's not running, launch it

```
cd /csl/ece882-040 && qemu-system-x86_64 -m 16384m -smp 8 -cpu host -accel kvm -display none \
  -nic user,model=virtio-net-pci,hostfwd=tcp::2240-:22 \
  -drive file=jammy-server-cloudimg-amd64-disk-kvm.img,format=qcow2 \
  -serial pty -monitor none \
  -daemonize -pidfile /csl/ece882-040/vm.pid 2>&1 | tee /csl/ece882-040/vm_launch.log
```

`-display none` is required (no GTK available in this headless
environment) — `-nographic` will fail under `-daemonize` since there's no
controlling terminal to attach it to.

The launch output (also saved to `vm_launch.log`) will print a line like:

```
char device redirected to /dev/pts/N (label serial0)
```

`/dev/pts/N` is the VM's serial console — this is how you talk to it.

## Step 3 — Find the console pty (if it was already running)

```
cat /csl/ece882-040/vm_launch.log   # look for "char device redirected to /dev/pts/N"
# or, if that log is gone:
lsof -p <qemu_pid> | grep /dev/pts
```

## Step 4 — Why not SSH?

`hostfwd=tcp::2240-:22` forwards a port for SSH, but **it doesn't work as
password auth**: this Ubuntu cloud image ships with `PasswordAuthentication
no` and no root login in `sshd_config`, and there's no SSH key set up for
password-less login either. SSH attempts fail with
`Permission denied (publickey)` before ever prompting for a password.
Fixing `sshd_config` itself requires editing a file inside the VM — which
requires the same console access described here, so it's not a real
shortcut. The console pty is the actual way in.

## Step 5 — Driving the console

The console is an interactive login shell over a raw pty — drive it with
Python's `pexpect.fdpexpect`, sending a command plus a trailing
`echo MARK_<random>_$?` so you can detect exactly when it finishes and
capture its exit code. Example pattern:

```python
import pexpect
import pexpect.fdpexpect as fdpexpect
import uuid

fd = open("/dev/pts/N", "r+b", buffering=0)
child = fdpexpect.fdspawn(fd, timeout=60)
marker = "MARK_" + uuid.uuid4().hex[:8]
child.send(f"<your command>; echo {marker}_$?\n")
child.expect(marker + r"_(\d+)", timeout=60)
exit_code = child.match.group(1).decode()
output = child.before.decode(errors="replace")
```

Notes:
- Long-running commands (e.g. a multi-minute pyperformance benchmark run)
  may exceed your read timeout — the command keeps running in the VM
  regardless; just re-open the pty and `child.expect()` the same marker
  again to pick up where you left off (nothing is lost).
- Writing files: use a heredoc (`cat > path << 'EOF' ... EOF`) rather than
  a single long echoed line — the pty's canonical mode has a per-line
  length limit (~4096 bytes), but a heredoc's individual lines stay short.
- To stop the VM: find its pid (`cat /csl/ece882-040/vm.pid` or `ps aux`)
  and `kill <pid>` (there's no monitor socket configured, so this is a
  hard stop, not a graceful in-guest shutdown — acceptable for this VM,
  since state persists in the qcow2 disk regardless).

## Project Repo

Cloned inside the VM at `/root/HW-SW-project-00460882`
(`git@github.com:ChrisShakkour/HW-SW-project-00460882.git`), pushed via an
SSH deploy key already present in the VM at `/root/.ssh/id_ed25519` (added
to the GitHub repo's deploy keys with write access — no further setup
needed to `git push` from inside the VM).

A separate clone also exists on the **host** at
`/csl/ece882-040/git/HW-SW-project-00460882` for local browsing/IDE use —
its remote is HTTPS with no stored push credentials, so pushes should go
through the VM's clone (which has the working deploy key), then
`git pull` on the host clone to sync down.

Read `README.md` in the repo for the full project plan, current progress,
and what's left to do.

