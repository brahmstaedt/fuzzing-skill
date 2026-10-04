# Demo runbook

## Before the demo (host)

    ./demo-setup.sh

## During the demo

1. Claude prepares the campaign (Claude Code):

       /fuzz-campaign dcmtk 3.6.3 fuzz the DICOM input parsing

2. Fuzz (host terminal, repo root):

       afl-fuzz -i campaigns/dcmtk-3.6.3/seeds -o out -m none -- campaigns/dcmtk-3.6.3/harness

3. Show the bug (second terminal, once the status screen shows a saved finding):

       campaigns/dcmtk-3.6.3/harness < out/default/crashes/id:000000*

## If something fails

- Step 1 fails: copy the rehearsed campaign folder from the external backup into campaigns/,
  then continue with step 2.
- Step 2 finds nothing in time: run step 3 against the saved rehearsal input in the backup
  instead, and say out loud that it is from a rehearsal run.
