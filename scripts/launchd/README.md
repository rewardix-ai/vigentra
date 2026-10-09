# Tracker LaunchAgent

Runs the live grid tracker (`scripts/collect_plates.py`, light mode on the Mac's GPU) as a macOS
LaunchAgent, so it starts again by itself after a reboot or log-in and after a crash. It waits for
Docker and central-api (opening Docker Desktop if needed), and does nothing after its `--until` deadline.

It cannot read while the Mac sleeps: keep it on power with the lid open.

Install (once):

```bash
cp scripts/launchd/com.vigentra.readers.plist ~/Library/LaunchAgents/
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vigentra.readers.plist
```

Status (`pid` set and `last exit code` 0 or absent):

```bash
launchctl print gui/$(id -u)/com.vigentra.readers | grep -E "state|pid|last exit"
```

Stop and remove (the stop takes about 45 s; readers flush their plates first):

```bash
launchctl bootout gui/$(id -u)/com.vigentra.readers
```

```bash
rm ~/Library/LaunchAgents/com.vigentra.readers.plist
```

Logs: `~/Library/Logs/vigentra-readers/supervisor.log`. Change the deadline or arguments in the plist,
then bootout and bootstrap again. Remove it after the event (13 Oct); after its deadline it only logs
"nothing to do" at each log-in.
