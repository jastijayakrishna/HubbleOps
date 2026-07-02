package loop

import (
	"context"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Each fileActionLedger instance has its own in-process mutex, so when many of them share
// one path they stand in for separate OS processes (the CI default). Only the cross-process
// file lock can make exactly one of them win the claim.
func TestFileLedgerClaimIsMutuallyExclusiveAcrossInstances(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.json")
	const n = 16
	var claimed int64
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ledger := &fileActionLedger{path: path}
			<-start
			status, _, err := ledger.Claim(context.Background(), "deploy:same", []byte(`{"k":1}`), time.Minute, "nonce")
			if err != nil {
				t.Errorf("claim: %v", err)
				return
			}
			if status == claimStatusClaimed {
				atomic.AddInt64(&claimed, 1)
			}
		}()
	}
	close(start)
	wg.Wait()
	if claimed != 1 {
		t.Fatalf("claimed=%d want exactly 1 (cross-process lock failed)", claimed)
	}
}

func TestFileLedgerInvalidateFreesCommittedKey(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.json")
	ledger := &fileActionLedger{path: path}
	ctx := context.Background()

	if status, _, err := ledger.Claim(ctx, "k", []byte(`{}`), time.Minute, "n"); err != nil || status != claimStatusClaimed {
		t.Fatalf("first claim status=%v err=%v", status, err)
	}
	if err := ledger.Commit(ctx, "k", []byte(`{}`), time.Hour, "n"); err != nil {
		t.Fatalf("commit: %v", err)
	}
	if status, _, _ := ledger.Claim(ctx, "k", []byte(`{}`), time.Minute, "n"); status != claimStatusCommitted {
		t.Fatalf("expected committed duplicate, got %v", status)
	}
	if err := ledger.Invalidate(ctx, "k"); err != nil {
		t.Fatalf("invalidate: %v", err)
	}
	if status, _, _ := ledger.Claim(ctx, "k", []byte(`{}`), time.Minute, "n"); status != claimStatusClaimed {
		t.Fatalf("after invalidate expected fresh claim, got %v", status)
	}
}

func TestFileLedgerCommitRequiresMatchingNonceForLivePendingClaim(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.json")
	ledger := &fileActionLedger{path: path}
	ctx := context.Background()

	if status, _, err := ledger.Claim(ctx, "k", []byte(`{"claim":"new"}`), time.Minute, "new-nonce"); err != nil || status != claimStatusClaimed {
		t.Fatalf("claim status=%v err=%v", status, err)
	}
	if err := ledger.Commit(ctx, "k", []byte(`{"stale":true}`), time.Hour, "old-nonce"); err == nil {
		t.Fatalf("stale commit with mismatched nonce succeeded")
	}
	status, previous, err := ledger.Claim(ctx, "k", []byte(`{"claim":"other"}`), time.Minute, "other-nonce")
	if err != nil {
		t.Fatalf("claim after stale commit: %v", err)
	}
	if status != claimStatusInFlight {
		t.Fatalf("status=%v want in-flight; previous=%s", status, previous)
	}
	if err := ledger.Commit(ctx, "k", []byte(`{"ok":true}`), time.Hour, "new-nonce"); err != nil {
		t.Fatalf("matching nonce commit: %v", err)
	}
	status, _, err = ledger.Claim(ctx, "k", []byte(`{}`), time.Minute, "any")
	if err != nil {
		t.Fatalf("claim after commit: %v", err)
	}
	if status != claimStatusCommitted {
		t.Fatalf("status=%v want committed", status)
	}
}
