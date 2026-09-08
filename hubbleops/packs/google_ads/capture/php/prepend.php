<?php
function hubbleops_google_ads_attest(): void
{
    $destination = getenv('HUBBLEOPS_INSTALL_PATH');
    $nonce = getenv('HUBBLEOPS_INSTALL_NONCE');
    if ($destination === false || $nonce === false) {
        return;
    }
    $record = json_encode(['language' => 'php', 'nonce' => $nonce], JSON_UNESCAPED_SLASHES);
    file_put_contents($destination, $record . "\n", FILE_APPEND | LOCK_EX);
}

function hubbleops_google_ads_capture(string $path, ?string $body = null): void
{
    $grpc = '/^\/?google\.ads\.googleads\.(v[0-9]+)\.services\.([A-Za-z][A-Za-z0-9]*Service)\/([A-Za-z][A-Za-z0-9]*)$/';
    $rest = '/^\/?(v[0-9]+)\/customers\/[^\/?]+\/googleAds:(searchStream|search|mutate)(?:\?.*)?$/';
    $matched = null;
    if (preg_match($grpc, $path, $parts) === 1) {
        $matched = [$parts[1], $parts[2], $parts[3], 'grpc'];
    } elseif (preg_match($rest, $path, $parts) === 1) {
        $methods = ['search' => 'Search', 'searchStream' => 'SearchStream', 'mutate' => 'Mutate'];
        $matched = [$parts[1], 'GoogleAdsService', $methods[$parts[2]], 'rest'];
    }
    $destination = getenv('HUBBLEOPS_EVENT_PATH');
    if ($matched === null || $destination === false) {
        return;
    }
    $frames = [];
    $trace = array_slice(debug_backtrace(DEBUG_BACKTRACE_IGNORE_ARGS), 1);
    foreach (array_slice($trace, 0, 255) as $frame) {
        $file = str_replace('\\', '/', $frame['file'] ?? '<runtime>/unknown');
        $kind = str_starts_with($file, '/workspace/') ? 'repository' : 'runtime';
        $file = $kind === 'repository' ? substr($file, 11) : '<runtime>/' . basename($file);
        $frames[] = ['kind' => $kind, 'path' => $file, 'line' => $frame['line'] ?? null, 'function' => $frame['function'] ?? null];
    }
    if (count($trace) > 255) {
        $frames[] = ['kind' => 'truncation', 'path' => '<runtime>/truncated', 'line' => null, 'function' => null, 'omitted' => count($trace) - 255];
    }
    $event = ['version' => $matched[0], 'service' => $matched[1], 'method' => $matched[2], 'request_text' => $body === null ? null : substr($body, 0, 65536), 'request_type' => $matched[3], 'stack' => $frames, 'ts' => gmdate('Y-m-d\TH:i:s\Z'), 'mode' => 'hook'];
    file_put_contents($destination, json_encode($event, JSON_UNESCAPED_SLASHES) . "\n", FILE_APPEND | LOCK_EX);
}

hubbleops_google_ads_attest();
