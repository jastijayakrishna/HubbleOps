package loop

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func TestRedisLedger_ConcurrentClaimsAndCommits(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { _ = rdb.Close() })
	stressActionStoreConcurrentClaims(t, NewActionStore(rdb), "redis")
}

func TestPostgresLedger_ConcurrentClaimsAndCommits(t *testing.T) {
	ctx, pool := postgresActionLedgerTestPool(t)
	stressActionStoreConcurrentClaims(t, NewPostgresActionStore(pool), "postgres")
	_ = ctx
}

func TestPostgresLedger_Heartbeat(t *testing.T) {
	ctx, pool := postgresActionLedgerTestPool(t)
	store := NewPostgresActionStore(pool).WithLease(30 * time.Millisecond)
	assertHeartbeatExtendsPendingLease(t, heartbeatBackend{
		name:    "postgres",
		store:   store,
		advance: func(d time.Duration) { time.Sleep(d) },
	})

	obs := heartbeatObservation("postgres-wrong-nonce")
	first, err := store.Decide(ctx, obs)
	if err != nil || first.Outcome != ActionOutcomeClaimed {
		t.Fatalf("first decide outcome=%q err=%v", first.Outcome, err)
	}
	if err := store.Heartbeat(ctx, obs.Project, obs.IdempotencyKey, "wrong-nonce"); !errors.Is(err, ErrLeaseNotHeld) {
		t.Fatalf("heartbeat wrong nonce err=%v want ErrLeaseNotHeld", err)
	}
	time.Sleep(45 * time.Millisecond)
	retry, err := store.Decide(ctx, obs)
	if err != nil {
		t.Fatalf("retry decide: %v", err)
	}
	if retry.Outcome != ActionOutcomeClaimed {
		t.Fatalf("retry outcome=%q want claimed after wrong nonce did not extend", retry.Outcome)
	}

	obs = heartbeatObservation("postgres-committed")
	first, err = store.Decide(ctx, obs)
	if err != nil || first.Outcome != ActionOutcomeClaimed {
		t.Fatalf("committed first decide outcome=%q err=%v", first.Outcome, err)
	}
	commitObs(t, store, obs, first.ClaimNonce, `{"ok":true}`)
	if err := store.Heartbeat(ctx, obs.Project, obs.IdempotencyKey, first.ClaimNonce); !errors.Is(err, ErrAlreadyCommitted) {
		t.Fatalf("heartbeat after commit err=%v want ErrAlreadyCommitted", err)
	}
}

func TestPostgresLedger_SweepExpired(t *testing.T) {
	ctx, pool := postgresActionLedgerTestPool(t)
	store := NewPostgresActionStore(pool)
	if _, err := pool.Exec(ctx, `
		INSERT INTO action_ledger (action_key, first_record, state, expires_at)
		SELECT 'expired-' || gs::text, '{}'::jsonb, 'committed', now() - interval '1 hour'
		FROM generate_series(1, 5001) gs;
		INSERT INTO action_ledger (action_key, first_record, state, expires_at)
		VALUES
			('live-1', '{}'::jsonb, 'pending', now() + interval '1 hour'),
			('live-2', '{}'::jsonb, 'committed', now() + interval '24 hours');
	`); err != nil {
		t.Fatalf("seed action_ledger: %v", err)
	}

	removed, err := store.SweepExpired(ctx)
	if err != nil {
		t.Fatalf("SweepExpired: %v", err)
	}
	if removed != 5001 {
		t.Fatalf("removed=%d want 5001", removed)
	}
	var expired, live int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM action_ledger WHERE action_key LIKE 'expired-%'`).Scan(&expired); err != nil {
		t.Fatalf("count expired: %v", err)
	}
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM action_ledger WHERE action_key LIKE 'live-%'`).Scan(&live); err != nil {
		t.Fatalf("count live: %v", err)
	}
	if expired != 0 || live != 2 {
		t.Fatalf("expired=%d live=%d, want expired=0 live=2", expired, live)
	}
}

func TestPostgresLedger_InvalidateOwned(t *testing.T) {
	_, pool := postgresActionLedgerTestPool(t)
	assertInvalidateOwnedRequiresDecisionID(t, "postgres", NewPostgresActionStore(pool))
}

func postgresActionLedgerTestPool(t *testing.T) (context.Context, *pgxpool.Pool) {
	t.Helper()
	dsn := strings.TrimSpace(os.Getenv("HUBBLEOPS_TEST_POSTGRES_DSN"))
	if dsn == "" {
		t.Skip("HUBBLEOPS_TEST_POSTGRES_DSN unset; skipping Postgres ledger tests")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	t.Cleanup(cancel)
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatalf("connect postgres: %v", err)
	}
	t.Cleanup(func() { pool.Close() })
	if _, err := pool.Exec(ctx, `
		CREATE TABLE IF NOT EXISTS action_ledger (
			action_key TEXT PRIMARY KEY,
			first_record JSONB NOT NULL,
			first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
			expires_at TIMESTAMPTZ NOT NULL,
			state TEXT NOT NULL DEFAULT 'committed'
		);
		CREATE INDEX IF NOT EXISTS idx_action_ledger_expires_at ON action_ledger (expires_at);
		TRUNCATE action_ledger;
	`); err != nil {
		t.Fatalf("prepare action_ledger: %v", err)
	}
	return ctx, pool
}

func stressActionStoreConcurrentClaims(t *testing.T, store *ActionStore, label string) {
	t.Helper()
	ctx := context.Background()
	const workers = 64
	start := make(chan struct{})
	var claimed int64
	var staleRejected int64
	var wg sync.WaitGroup
	errs := make(chan error, workers)
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			obs := backendLedgerObservation(label, i)
			decision, err := store.Decide(ctx, obs)
			if err != nil {
				errs <- fmt.Errorf("decide %d: %w", i, err)
				return
			}
			switch decision.Outcome {
			case ActionOutcomeClaimed:
				atomic.AddInt64(&claimed, 1)
				time.Sleep(150 * time.Millisecond)
				if err := store.Commit(ctx, ActionResult{
					Project:        obs.Project,
					IdempotencyKey: obs.IdempotencyKey,
					ClaimNonce:     decision.ClaimNonce,
					ToolName:       obs.ToolName,
					ActionRisk:     obs.ActionRisk,
					ResourceID:     obs.ResourceID,
					DecisionID:     "winner-" + label,
					ResultClass:    "success",
				}); err != nil {
					errs <- fmt.Errorf("commit winner %d: %w", i, err)
				}
			case ActionOutcomeInFlight:
				err := store.Commit(ctx, ActionResult{
					Project:        obs.Project,
					IdempotencyKey: obs.IdempotencyKey,
					ClaimNonce:     fmt.Sprintf("stale-%d", i),
					ToolName:       obs.ToolName,
					ActionRisk:     obs.ActionRisk,
					ResourceID:     obs.ResourceID,
					DecisionID:     fmt.Sprintf("stale-%d", i),
					ResultClass:    "success",
				})
				if err != nil {
					atomic.AddInt64(&staleRejected, 1)
				}
			case ActionOutcomeCommittedReplay:
			default:
				errs <- fmt.Errorf("worker %d unexpected outcome %q", i, decision.Outcome)
			}
		}(i)
	}
	close(start)
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	if claimed != 1 {
		t.Fatalf("%s claimed=%d want exactly 1", label, claimed)
	}
	if staleRejected == 0 {
		t.Fatalf("%s stress did not exercise fenced stale commit", label)
	}
	final, err := store.Decide(ctx, backendLedgerObservation(label, workers+1))
	if err != nil {
		t.Fatalf("%s final decide: %v", label, err)
	}
	if final.Outcome != ActionOutcomeCommittedReplay || final.Replay == nil || final.Replay.DecisionID != "winner-"+label {
		t.Fatalf("%s final replay=%+v outcome=%q", label, final.Replay, final.Outcome)
	}
}

func backendLedgerObservation(label string, i int) ActionObservation {
	return ActionObservation{
		Project:        "proj-" + label,
		SessionID:      fmt.Sprintf("sess-%s-%d", label, i),
		ToolName:       "deploy.release",
		ActionRisk:     ActionRiskDangerous,
		IdempotencyKey: "deploy:shared-key",
		ResourceID:     "service/" + label,
	}
}
