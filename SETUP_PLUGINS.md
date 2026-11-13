# Installing the four plugins (Windows, Claude Code in VS Code)

Run the slash commands inside a Claude Code session. Read each plugin's files on GitHub before installing: plugins run with Claude Code's permissions, and some add hooks that run at session start.

## 1. Superpowers (the working method)

```
/plugin install superpowers@claude-plugins-official
```

## 2. Karpathy guidelines (coding discipline)

```
/plugin marketplace add forrestchang/andrej-karpathy-skills
/plugin install andrej-karpathy-skills@karpathy-skills
```

Use the plugin, not the option that appends its CLAUDE.md to ours. Our CLAUDE.md stays the project's single source of rules.

## 3. i-have-adhd (reply style)

```
/plugin marketplace add ayghri/i-have-adhd
/plugin install i-have-adhd@i-have-adhd
```

Turn it on in a session with `/i-have-adhd`; stop with "normal mode". To have it on in every session, in PowerShell:

```
New-Item -ItemType File -Force "$HOME\.claude\.i-have-adhd-always"
```

## 4. Claude Octopus (multi-model review, off by default)

```
/plugin marketplace add https://github.com/nyldn/claude-octopus.git
/plugin install octo@nyldn-plugins
/octo:setup
```

In setup, keep the Claude provider only. Then disable it until a phase review:

```
/plugin disable octo
```

Enable it for the review, then disable it again.

## Restart

Fully quit and reopen Claude Code (and VS Code) after installing, then check with `/plugin`.
