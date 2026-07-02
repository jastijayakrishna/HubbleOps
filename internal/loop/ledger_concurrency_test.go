package loop

import (
	"context"
	"fmt"
	"sync"
	"testing"
)

// TestActionStoreConcurrentLifecycleNoPanic hammers the full claim/commit/release/invalidate
// lifecycle from many goroutines over a small key space, so interleavings (deadlock, panic,
// nil deref, inconsistent state) surface. Pair with `-race` on a cgo-enabled host for data
// races; here it guards the logic and liveness.
func TestActionStoreConcurrentLifecycleNoPanic(t *testing.T) {
	store := NewMemoryActionStore()
	ctx := context.Background()
	var wg sync.WaitGroup
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			key := fmt.Sprintf("deploy:%d", i%6)
			decision, err := store.Decide(ctx, ActionObservation{
				Project:        "p",
				IdempotencyKey: key,
				ToolName:       "deploy",
				ActionRisk:     "write",
			})
			if err != nil {
				t.Errorf("decide: %v", err)
				return
			}
			switch i % 4 {
			case 0:
				_ = store.Commit(ctx, ActionResult{Project: "p", IdempotencyKey: key, ToolName: "deploy", ActionRisk: "write", ResultClass: "success"})
			case 1:
				_ = store.Release(ctx, "p", key, decision.ClaimNonce)
			case 2:
				_ = store.Invalidate(ctx, "p", key)
			}
		}(i)
	}
	wg.Wait()

	// The store must remain usable after the storm.
	if _, err := store.Decide(ctx, ActionObservation{Project: "p", IdempotencyKey: "fresh", ToolName: "deploy", ActionRisk: "write"}); err != nil {
		t.Fatalf("store unusable after concurrency: %v", err)
	}
}
