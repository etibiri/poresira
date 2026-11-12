<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# CLA Assistant setup (maintainers)

External contributions require a signed [CLA](https://github.com/etibiri/poresira/blob/main/CLA.md).
Signatures are collected by the [CLA Assistant](https://cla-assistant.io) GitHub App. Its
configuration lives in the service, not in the repository, so it must be done once by the
repository owner. Screens may differ slightly from these steps.

1. Publish the CLA text where CLA Assistant can read it: create a public GitHub Gist whose
   content is `CLA.md`. Keep it identical to the file in the repository; when `CLA.md` changes,
   update the Gist and let CLA Assistant ask existing contributors to sign again.
2. Sign in at cla-assistant.io with the GitHub account that owns `etibiri/poresira`.
3. Choose **Configure CLA**, select the `etibiri/poresira` repository and the Gist.
4. Leave the default sign phrase ("I have read the CLA Document and I hereby sign the CLA")
   and enable the request for contributor details only if you need them.
5. Add bots that do not sign, such as `dependabot[bot]`, to the allow list. Sign the CLA
   yourself too if you want your own pull requests to pass the check.
6. In the repository's **Settings > Branches**, protect `main` and require the status check
   `license/cla` (it appears after the first pull request).

Project rule: never merge an external contribution without a signed CLA.
