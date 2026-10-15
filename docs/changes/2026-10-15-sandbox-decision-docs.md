# Writing down the sandbox decision

**Step:** 09 (revisited) · **Commit:** docs(adr): record the sandbox engine decision (ADR 0021) · **Date:** 2026-10-15

## What changed
ADR 0021 records the decision the owner delegated: Firecracker implemented and Kata verified on
KVM, gVisor still the default, the managed sandbox service a stub by decision until a vendor
passes privacy and Australian data-residency review. ADR 0019 now points to it. The Step 9
chapter shows the CI evidence (21 tests passed on Firecracker and Kata), `docs/setup/local.md`
explains how to run both engines on an Apple M3-or-later Mac through a Lima VM with nested
virtualisation, and PROGRESS closes open issue 7.

## Why
A decision nobody wrote down gets made again, differently, by the next person. The reasons
(isolation strength, verifiability, where your data goes) are the useful part.

## How to check it
Read `docs/adr/0021-sandbox-engines-decision.md`; the CI job "sandbox (Firecracker, Kata; KVM)"
on the latest push shows the evidence.

## Risks and follow-ups
The Mac instructions are VERIFY-ON-MAC until run on your M5 Max.
