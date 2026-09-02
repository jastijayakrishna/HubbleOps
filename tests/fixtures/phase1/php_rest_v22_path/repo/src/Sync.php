<?php

namespace Acme\AdsSync;

use GuzzleHttp\Client;

class Sync
{
    private const ENDPOINT = 'https://googleads.googleapis.com/v22/customers/';

    public function fetch(string $customerId, string $token): string
    {
        $client = new Client();
        $response = $client->post(self::ENDPOINT . $customerId . '/googleAds:search', [
            'headers' => ['developer-token' => $token],
            'json' => ['query' => 'SELECT campaign.id FROM campaign'],
        ]);

        return (string) $response->getBody();
    }
}
