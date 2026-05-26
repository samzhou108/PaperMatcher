#!/bin/bash
# Manual backup script — run before major edits to create a git checkpoint
# Usage: ./backup.sh "describe what you're about to change"

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
MESSAGE="${1:-Backup at $TIMESTAMP}"

echo "Creating backup: $MESSAGE"
git add -A
git commit -m "BACKUP: $MESSAGE" && echo "✓ Backup created" || echo "✗ No changes to backup"
