<?php

function reporting_gateway_search(string $query): void
{
    hubbleops_google_ads_capture(
        '/google.ads.googleads.v23.services.GoogleAdsService/Search',
        $query
    );
}

function daily_campaign_report(): void
{
    reporting_gateway_search('SELECT campaign.id FROM campaign');
}

daily_campaign_report();
