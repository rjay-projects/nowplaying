#!/usr/bin/env bash
set -e

echo "=== nowplaying installer ==="

# --- chafa (renders album art as colored terminal blocks) ---
if ! command -v chafa >/dev/null 2>&1; then
    echo "Installing chafa..."
    if command -v apt >/dev/null 2>&1; then
        sudo apt update && sudo apt install -y chafa
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y chafa
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm chafa
    elif command -v brew >/dev/null 2>&1; then
        brew install chafa
    else
        echo "Could not detect your package manager - please install 'chafa' manually."
        echo "(The app still works without it, just without album art.)"
    fi
else
    echo "chafa already installed."
fi

INSTALL_DIR="/opt/nowplaying"
echo "Installing to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp nowplaying.py authorize.py "$INSTALL_DIR/"
sudo chown -R "$(whoami)" "$INSTALL_DIR"

echo "Creating 'nowplaying' command..."
sudo tee /usr/local/bin/nowplaying > /dev/null << EOF
#!/bin/bash
exec python3 $INSTALL_DIR/nowplaying.py "\$@"
EOF
sudo chmod +x /usr/local/bin/nowplaying

echo ""
echo "Install complete."
echo ""
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo "Next step - authorize with Spotify (one-time):"
    echo "  cd $INSTALL_DIR && python3 authorize.py"
else
    echo "Existing .env found - you're ready to go. Just run: nowplaying"
fi
