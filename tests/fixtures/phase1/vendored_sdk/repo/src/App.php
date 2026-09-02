<?php

namespace Acme\Legacy;

use Google\Ads\GoogleAds\V22\Services\GoogleAdsServiceClient;

class App
{
    public function service(GoogleAdsServiceClient $client): GoogleAdsServiceClient
    {
        return $client;
    }
}
