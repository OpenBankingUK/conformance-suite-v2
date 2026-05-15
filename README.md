# Functional Conformance Suite V2 (WIP)

This forked repository will be used to manage the development of the new v2 Functional Confirmance suite before it is ready to be merged back into the main conformance suite repository.

This repository is purely for development and should not be used as the official conformance suite.

Please see the official repo for the Open Banking Conformance Suite [here](https://github.com/OpenBankingUK/conformance-suite).

## Model-bank smoke check

The first Ozone model-bank interaction is available as a small manual runner. It reads a JSON config and fetches the OpenID discovery document. The runner can also fetch the discovered JWKS endpoint when `followUp.mode` is set to `jwks` and the required certificate trust chain is available locally.

```bash
uv run python main.py config/model-bank-example.json
```

The runner writes a structured result JSON to the configured `resultOutputPath` and exits with `0` when all smoke-check steps pass, `1` when the model-bank check fails, or `2` when the config is invalid. Relative result paths are resolved from the current working directory, while certificate paths are resolved from the config file location.

The config is JSON-only for now. Certificate paths, when supplied, are resolved under `tls.certificatePathRoot`; do not commit real certificates, private keys, or inline secret material.
