package main

// Exit codes are a CLI contract consumed by CI pipelines: a gate decision and a
// HubbleOps failure must never share a code, or automation cannot tell "this
// deploy was blocked" from "the firewall itself broke".
//
//	0 = allow, or the command succeeded
//	1 = block — verify-receipts, evidence-pack, and policy validate also use it
//	    for a failed check (unverified receipts, invalid policy)
//	2 = usage or flag error; nothing was executed
//	3 = require_approval
//	4 = internal error: receipt write failure, ledger unavailable, IO error —
//	    no decision was produced, treat the action as not cleared to run
const (
	exitAllow           = 0
	exitBlock           = 1
	exitUsage           = 2
	exitRequireApproval = 3
	exitInternalError   = 4
)
