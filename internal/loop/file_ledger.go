package loop

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/hubbleops/hubbleops/internal/filelock"
)

// NewFileActionStore stores ActionStore claims in a local JSON file. It is meant for
// CLI/CI preflight paths where Redis or Postgres is not available but duplicate deploy
// keys still need to survive separate process invocations.
func NewFileActionStore(path string) *ActionStore {
	return &ActionStore{ledger: &fileActionLedger{path: path}}
}

type fileActionLedger struct {
	path string
	mu   sync.Mutex
	now  func() time.Time
}

type fileActionItem struct {
	Value             string `json:"value"`
	State             string `json:"state"`
	Nonce             string `json:"nonce,omitempty"`
	ExpiresAtUnixNano int64  `json:"expires_at_unix_nano,omitempty"`
}

func (l *fileActionLedger) Claim(ctx context.Context, key string, pending []byte, lease time.Duration, nonce string) (claimStatus, string, error) {
	if err := ctx.Err(); err != nil {
		return claimStatusClaimed, "", err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	unlock, err := l.lockFile()
	if err != nil {
		return claimStatusClaimed, "", err
	}
	defer unlock()

	now := l.nowTime()
	items, _, err := l.load(now)
	if err != nil {
		return claimStatusClaimed, "", err
	}
	if item, ok := items[key]; ok {
		if item.ExpiresAtUnixNano == 0 || time.Unix(0, item.ExpiresAtUnixNano).After(now) {
			if item.State == ledgerStateCommitted {
				return claimStatusCommitted, item.Value, nil
			}
			return claimStatusInFlight, item.Value, nil
		}
		delete(items, key)
	}

	expiresAt := int64(0)
	if lease > 0 {
		expiresAt = now.Add(lease).UnixNano()
	}
	items[key] = fileActionItem{Value: string(pending), State: ledgerStatePending, Nonce: nonce, ExpiresAtUnixNano: expiresAt}
	if err := l.save(items); err != nil {
		return claimStatusClaimed, "", err
	}
	return claimStatusClaimed, "", nil
}

func (l *fileActionLedger) Commit(ctx context.Context, key string, committed []byte, window time.Duration, nonce string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	unlock, err := l.lockFile()
	if err != nil {
		return err
	}
	defer unlock()

	now := l.nowTime()
	items, _, err := l.load(now)
	if err != nil {
		return err
	}
	var pending string
	if item, ok := items[key]; ok {
		if item.State == ledgerStateCommitted {
			if nonce != "" {
				return fmt.Errorf("claim nonce mismatch")
			}
			return nil
		}
		if item.State == ledgerStatePending && item.Nonce != "" && item.Nonce != nonce {
			return fmt.Errorf("claim nonce mismatch")
		}
		if item.State == ledgerStatePending {
			pending = item.Value
		}
	}
	committed, err = carryForwardRequestFingerprint(committed, pending)
	if err != nil {
		return err
	}
	expiresAt := int64(0)
	if window > 0 {
		expiresAt = now.Add(window).UnixNano()
	}
	items[key] = fileActionItem{Value: string(committed), State: ledgerStateCommitted, ExpiresAtUnixNano: expiresAt}
	return l.save(items)
}

func (l *fileActionLedger) Release(ctx context.Context, key, nonce string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	unlock, err := l.lockFile()
	if err != nil {
		return err
	}
	defer unlock()

	now := l.nowTime()
	items, removed, err := l.load(now)
	if err != nil {
		return err
	}
	changed := removed > 0
	if item, ok := items[key]; ok && item.State == ledgerStatePending && (item.Nonce == "" || item.Nonce == nonce) {
		delete(items, key)
		changed = true
	}
	if changed {
		return l.save(items)
	}
	return nil
}

func (l *fileActionLedger) Extend(ctx context.Context, key string, lease time.Duration, nonce string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	requestTime := l.nowTime()
	l.mu.Lock()
	defer l.mu.Unlock()
	unlock, err := l.lockFile()
	if err != nil {
		return err
	}
	defer unlock()

	items, _, err := l.load(requestTime)
	if err != nil {
		return err
	}
	item, ok := items[key]
	if !ok {
		return ErrLeaseNotHeld
	}
	if item.State == ledgerStateCommitted {
		return ErrAlreadyCommitted
	}
	if item.State != ledgerStatePending || (item.Nonce != "" && item.Nonce != nonce) {
		return ErrLeaseNotHeld
	}
	item.ExpiresAtUnixNano = l.nowTime().Add(lease).UnixNano()
	items[key] = item
	return l.save(items)
}

// Invalidate removes a key regardless of state. It is the legacy unowned path; callers
// that have a receipt decision ID should use InvalidateOwned.
func (l *fileActionLedger) Invalidate(ctx context.Context, key string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	unlock, err := l.lockFile()
	if err != nil {
		return err
	}
	defer unlock()

	items, _, err := l.load(l.nowTime())
	if err != nil {
		return err
	}
	if _, ok := items[key]; !ok {
		return nil
	}
	delete(items, key)
	return l.save(items)
}

func (l *fileActionLedger) InvalidateOwned(ctx context.Context, key, decisionID string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	unlock, err := l.lockFile()
	if err != nil {
		return err
	}
	defer unlock()

	items, _, err := l.load(l.nowTime())
	if err != nil {
		return err
	}
	item, ok := items[key]
	if !ok || item.State != ledgerStateCommitted || decisionIDFromRecord(item.Value) != decisionID {
		return nil
	}
	delete(items, key)
	return l.save(items)
}

func (l *fileActionLedger) SweepExpired(ctx context.Context) (int64, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	unlock, err := l.lockFile()
	if err != nil {
		return 0, err
	}
	defer unlock()

	_, removed, err := l.load(l.nowTime())
	if err != nil {
		return 0, err
	}
	return removed, nil
}

// lockFile takes an OS-level advisory lock around the read-modify-write so that two
// separate processes (the CI default — each `preflight` is a fresh process) cannot both
// observe "no claim" and both write. The in-process mutex alone does not cross processes.
func (l *fileActionLedger) lockFile() (func(), error) {
	return filelock.Acquire(l.path + ".lock")
}

func (l *fileActionLedger) nowTime() time.Time {
	if l.now != nil {
		return l.now()
	}
	return time.Now()
}

func (l *fileActionLedger) load(now time.Time) (map[string]fileActionItem, int64, error) {
	path := filepath.Clean(l.path)
	if path == "." || path == "" {
		return nil, 0, errors.New("action ledger path is required")
	}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]fileActionItem{}, 0, nil
	}
	if err != nil {
		return nil, 0, err
	}
	items := map[string]fileActionItem{}
	if len(data) > 0 {
		if err := json.Unmarshal(data, &items); err != nil {
			return nil, 0, err
		}
	}
	var removed int64
	for key, item := range items {
		if item.ExpiresAtUnixNano > 0 && !time.Unix(0, item.ExpiresAtUnixNano).After(now) {
			delete(items, key)
			removed++
		}
	}
	if removed > 0 {
		if err := l.save(items); err != nil {
			return nil, 0, err
		}
	}
	return items, removed, nil
}

func (l *fileActionLedger) save(items map[string]fileActionItem) error {
	path := filepath.Clean(l.path)
	if path == "." || path == "" {
		return errors.New("action ledger path is required")
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(items, "", "  ")
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".action-ledger-*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		_ = os.Remove(tmpName)
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		_ = os.Remove(tmpName)
		return err
	}
	return os.Chmod(path, 0600)
}
