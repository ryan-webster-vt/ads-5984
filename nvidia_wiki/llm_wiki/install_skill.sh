#!/bin/bash

# Installation script for the LLM Wiki skill
# Usage: ./install_skill.sh
# Installs into ~/.claude/skills/llm_wiki

set -e  # Exit on error

TARGET_BASE="$HOME/.claude/skills"
TARGET_DIR="$TARGET_BASE/llm_wiki"

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "Source directory: $SCRIPT_DIR"
echo "Target directory: $TARGET_DIR"
echo ""

# Remove existing skill if it exists
if [ -d "$TARGET_DIR" ]; then
    echo "Removing existing skill installation..."
    rm -rf "$TARGET_DIR"
    echo "✓ Existing skill removed"
    echo ""
fi

# Create target directory structure
echo "Creating target directories..."
mkdir -p "$TARGET_DIR"
mkdir -p "$TARGET_DIR/references"
mkdir -p "$TARGET_DIR/assets"

# Copy main files
echo "Copying files..."

for file in SKILL.md README.md; do
    if [ -f "$SCRIPT_DIR/$file" ]; then
        echo "  - $file"
        cp "$SCRIPT_DIR/$file" "$TARGET_DIR/"
    else
        echo "  ! Warning: $file not found"
    fi
done

# Copy references
if [ -d "$SCRIPT_DIR/references" ]; then
    echo "  - references/"
    cp "$SCRIPT_DIR/references/"*.md "$TARGET_DIR/references/" 2>/dev/null || true
else
    echo "  ! Warning: references/ directory not found"
fi

# Copy assets
if [ -d "$SCRIPT_DIR/assets" ]; then
    echo "  - assets/"
    cp -r "$SCRIPT_DIR/assets/"* "$TARGET_DIR/assets/" 2>/dev/null || true
fi

# Remove __pycache__ if it exists
find "$TARGET_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo ""
echo "✓ Installation complete!"
echo ""
echo "Skill installed to: $TARGET_DIR"
echo ""
echo "Next steps:"
echo "1. Restart Claude Code (if running)"
echo "2. Drop a source into raw/ and ask: 'Ingest this source into the wiki.'"
echo ""
echo "For OpenCode, copy assets/AGENTS.md into your project root."
echo ""
