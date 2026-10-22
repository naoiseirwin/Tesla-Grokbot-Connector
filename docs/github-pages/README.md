# GitHub Pages files for Tesla partner registration

Copy these files into the repository that serves your domain. Do not put a
private key in that repository.

Tesla's partner registration fetches:

`https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem`

For a user site such as `naoiseirwin.github.io`:

1. Add an empty `.nojekyll` file at the site root so GitHub Pages serves
   `/.well-known/`.
2. Copy the **public** key to
   `.well-known/appspecific/com.tesla.3p.public-key.pem`.
3. Copy `callback.html` to the site root if you want the paste fallback.
   Register `https://<domain>/callback.html` as a redirect URI.

Whether Tesla accepts a `github.io` domain is unconfirmed, because `github.io`
is a public-suffix domain. If registration fails, use a domain you own.

Generate the key pair outside this repo:

```bash
scripts/generate-keypair.sh "$HOME/tesla-partner-keys"
```

Keep `private-key.pem` offline. Nothing in this connector signs with it.
