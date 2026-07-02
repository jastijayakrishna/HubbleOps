package loop

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestFileLedger_ConcurrentProcesses(t *testing.T) {
	if os.Getenv("HUBBLEOPS_FILE_LEDGER_HELPER") == "1" {
		t.Skip("helper is run directly by the parent test")
	}
	dir := t.TempDir()
	ledgerPath := filepath.Join(dir, "action-ledger.json")
	startPath := filepath.Join(dir, "start")
	const processes = 24

	type childResult struct {
		Index   string `json:"index"`
		Outcome string `json:"outcome"`
		Error   string `json:"error,omitempty"`
	}
	type childProc struct {
		cmd    *exec.Cmd
		output *bytes.Buffer
	}
	children := make([]childProc, 0, processes)
	for i := 0; i < processes; i++ {
		cmd := exec.Command(os.Args[0], "-test.run=^TestFileLedgerProcessHelper$")
		cmd.Env = append(os.Environ(),
			"HUBBLEOPS_FILE_LEDGER_HELPER=1",
			"HUBBLEOPS_FILE_LEDGER_PATH="+ledgerPath,
			"HUBBLEOPS_FILE_LEDGER_START="+startPath,
			"HUBBLEOPS_FILE_LEDGER_INDEX="+string(rune('A'+i)),
		)
		out := &bytes.Buffer{}
		cmd.Stdout = out
		cmd.Stderr = out
		if err := cmd.Start(); err != nil {
			t.Fatalf("start helper %d: %v", i, err)
		}
		children = append(children, childProc{cmd: cmd, output: out})
	}
	if err := os.WriteFile(startPath, []byte("go"), 0o600); err != nil {
		t.Fatalf("release helpers: %v", err)
	}

	claimed := 0
	staleRejected := 0
	for i := range children {
		err := children[i].cmd.Wait()
		raw := strings.TrimSpace(children[i].output.String())
		if err != nil {
			t.Fatalf("helper %d failed: %v output=%s", i, err, raw)
		}
		var res childResult
		jsonLine := firstJSONLine(raw)
		if err := json.Unmarshal([]byte(jsonLine), &res); err != nil {
			t.Fatalf("helper %d invalid JSON %q: %v", i, raw, err)
		}
		switch res.Outcome {
		case "claimed":
			claimed++
		case "stale_rejected":
			staleRejected++
		case "committed_replay":
		default:
			t.Fatalf("helper %d unexpected result %+v", i, res)
		}
	}
	if claimed != 1 {
		t.Fatalf("claimed=%d want exactly one", claimed)
	}
	if staleRejected == 0 {
		t.Fatalf("no helper observed a fenced stale commit; stress did not exercise nonce mismatch")
	}

	store := NewFileActionStore(ledgerPath)
	dup, err := store.Decide(context.Background(), processLedgerObservation("final"))
	if err != nil {
		t.Fatalf("final duplicate decide: %v", err)
	}
	if dup.Outcome != ActionOutcomeCommittedReplay {
		t.Fatalf("final outcome=%q want committed replay; decision=%+v", dup.Outcome, dup.Decision)
	}
	if dup.Replay == nil || !strings.HasPrefix(dup.Replay.DecisionID, "winner-") {
		t.Fatalf("final replay=%+v, want winner decision id", dup.Replay)
	}
}

func TestFileLedgerProcessHelper(t *testing.T) {
	if os.Getenv("HUBBLEOPS_FILE_LEDGER_HELPER") != "1" {
		return
	}
	ledgerPath := os.Getenv("HUBBLEOPS_FILE_LEDGER_PATH")
	startPath := os.Getenv("HUBBLEOPS_FILE_LEDGER_START")
	index := os.Getenv("HUBBLEOPS_FILE_LEDGER_INDEX")
	deadline := time.Now().Add(10 * time.Second)
	for {
		if _, err := os.Stat(startPath); err == nil {
			break
		}
		if time.Now().After(deadline) {
			printLedgerHelperResult(index, "timeout", "start file never appeared")
			return
		}
		time.Sleep(2 * time.Millisecond)
	}

	ctx := context.Background()
	store := NewFileActionStore(ledgerPath)
	decision, err := store.Decide(ctx, processLedgerObservation(index))
	if err != nil {
		printLedgerHelperResult(index, "error", err.Error())
		return
	}
	switch decision.Outcome {
	case ActionOutcomeClaimed:
		time.Sleep(300 * time.Millisecond)
		err := store.Commit(ctx, ActionResult{
			Project:        "proj",
			IdempotencyKey: "deploy:race-key",
			ClaimNonce:     decision.ClaimNonce,
			ToolName:       "deploy.release",
			ActionRisk:     ActionRiskDangerous,
			ResourceID:     "service/race",
			DecisionID:     "winner-" + index,
			ResultClass:    "success",
		})
		if err != nil {
			printLedgerHelperResult(index, "error", err.Error())
			return
		}
		printLedgerHelperResult(index, "claimed", "")
	case ActionOutcomeInFlight:
		err := store.Commit(ctx, ActionResult{
			Project:        "proj",
			IdempotencyKey: "deploy:race-key",
			ClaimNonce:     "stale-" + index,
			ToolName:       "deploy.release",
			ActionRisk:     ActionRiskDangerous,
			ResourceID:     "service/race",
			DecisionID:     "stale-" + index,
			ResultClass:    "success",
		})
		if err == nil {
			printLedgerHelperResult(index, "error", "stale commit unexpectedly succeeded")
			return
		}
		printLedgerHelperResult(index, "stale_rejected", "")
	case ActionOutcomeCommittedReplay:
		printLedgerHelperResult(index, "committed_replay", "")
	default:
		printLedgerHelperResult(index, "error", "unexpected outcome "+decision.Outcome)
	}
}

func processLedgerObservation(index string) ActionObservation {
	return ActionObservation{
		Project:        "proj",
		SessionID:      "sess-" + index,
		ToolName:       "deploy.release",
		ActionRisk:     ActionRiskDangerous,
		IdempotencyKey: "deploy:race-key",
		ResourceID:     "service/race",
	}
}

func firstJSONLine(raw string) string {
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "{") {
			return line
		}
	}
	return raw
}

func printLedgerHelperResult(index, outcome, errText string) {
	result := map[string]string{"index": index, "outcome": outcome}
	if errText != "" {
		result["error"] = errText
	}
	data, _ := json.Marshal(result)
	_, _ = os.Stdout.Write(append(data, '\n'))
}
