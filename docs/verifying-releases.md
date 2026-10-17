# Verifying releases

Each tagged release (`vX.Y.Z`) publishes: Python wheel and sdist, a container image on GHCR (mirrored to Docker Hub with the same digest), `SHA256SUMS`, an SBOM (SPDX JSON), and build provenance attestations. Artifacts are signed with [Sigstore cosign](https://docs.sigstore.dev/) keyless signing tied to this repository's release workflow; there is no long-lived signing key.

> This is the process the release workflow implements. Run it against your first real release to confirm.

## Checksums

```bash
sha256sum -c SHA256SUMS
```

## Signatures

Every file in the release has a matching `.sigstore.json` bundle.

```bash
cosign verify-blob \
  --bundle homelabscribe-X.Y.Z.tar.gz.sigstore.json \
  --certificate-identity-regexp '^https://github.com/HoneyBearTech/homelabscribe/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  homelabscribe-X.Y.Z.tar.gz

cosign verify ghcr.io/honeybeartech/homelabscribe:X.Y.Z \
  --certificate-identity-regexp '^https://github.com/HoneyBearTech/homelabscribe/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Provenance and SBOM

Build provenance is published as GitHub artifact attestations:

```bash
gh attestation verify homelabscribe-X.Y.Z.tar.gz --repo HoneyBearTech/homelabscribe
gh attestation verify oci://ghcr.io/honeybeartech/homelabscribe:X.Y.Z --repo HoneyBearTech/homelabscribe
# The Docker Hub copy is the same image (identical digest), so this works for it too:
gh attestation verify oci://docker.io/honeybeartech/homelabscribe:X.Y.Z --repo HoneyBearTech/homelabscribe
```

`sbom.spdx.json` lists the HomelabScribe wheel and every Python package that `pip install` pulled in alongside it, with their versions.

Release notes list fixed vulnerabilities. Found a mismatch? Treat it as a security issue and follow [SECURITY.md](../SECURITY.md).
