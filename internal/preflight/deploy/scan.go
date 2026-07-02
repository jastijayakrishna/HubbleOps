package deploy

import (
	"strings"

	"github.com/hubbleops/hubbleops/internal/action"
	"github.com/hubbleops/hubbleops/internal/preflight"
	"github.com/hubbleops/hubbleops/internal/privacy"
)

const (
	ActionRelease = "deploy.release"
	DefaultRisk   = "tier_2"
)

type Options struct {
	Service     string
	Environment string
	ServiceRisk string
}

func Scan(opts Options) []preflight.Finding {
	service := strings.TrimSpace(opts.Service)
	if service == "" {
		return nil
	}
	env := normalizeEnvironment(opts.Environment)
	riskTier := NormalizeRisk(opts.ServiceRisk)
	score := RiskScore(riskTier, env)
	return []preflight.Finding{{
		Source:    preflight.SourceDeploy,
		Kind:      preflight.KindDeployRelease,
		Action:    ActionRelease,
		Target:    service,
		RiskScore: score,
		RiskClass: action.RiskClass(score),
		Evidence: []string{
			"deploy_action=release",
			"deploy_environment=" + env,
			"service_risk=" + riskTier,
			"service_fingerprint=" + privacy.FingerprintString(service),
		},
		ChangeTags: []string{
			"deploy:release",
			"environment:" + env,
			"service_risk:" + riskTier,
		},
	}}
}

func NormalizeRisk(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	switch value {
	case "", "unknown":
		return DefaultRisk
	default:
		return value
	}
}

func RiskScore(riskTier, env string) int {
	riskTier = NormalizeRisk(riskTier)
	production := normalizeEnvironment(env) == "production"
	switch riskTier {
	case "tier_0", "tier0", "critical":
		if production {
			return 85
		}
		return 70
	case "tier_1", "tier1", "high":
		if production {
			return 72
		}
		return 55
	case "tier_2", "tier2", "medium":
		if production {
			return 45
		}
		return 30
	case "tier_3", "tier3", "low":
		if production {
			return 25
		}
		return 15
	default:
		if production {
			return 50
		}
		return 30
	}
}

func normalizeEnvironment(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "prod":
		return "production"
	case "dev":
		return "development"
	case "":
		return "unknown"
	default:
		return strings.ToLower(strings.TrimSpace(value))
	}
}
