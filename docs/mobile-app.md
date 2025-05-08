# Mobile Application support

FCS allows testing applications that allows mobile interactions only and cannot redirect to localhost without tunneling
software.

To allow this OBL provides a proxy that allows storing consent callback parameters. This field is added to the Discovery model and the redirect_url in the config must also be set to use the remote callback handler

## Configuration

Sample configuration is provided. This configuration uses Ozone model Bank through a mobile browser.

Key elements of the discovery file are:

```json5
{
  "discoveryModel": {
    "name": "ob-v3.1-ozone",
    "description": "O3 Mobile PSU consent flow. An Open Banking UK discovery template for v3.1 of Accounts and Payments with pre-populated model Bank (Ozone) data.",
    "discoveryVersion": "v0.4.0",

    //Required for mobile app support
    "tokenAcquisition": "mobile",
    
    //Required for mobile app support
    "callbackProxyUrl": "https://fcs-consent-callback.openbanking.rocks",
    "discoveryItems": [
      {
        //...
      }
    ]
  }
}

```

Redirect URL in the configuration parameters:

```json5
{
"redirect_url": "https://fcs-consent-callback.openbanking.rocks/callback-handler"
}

