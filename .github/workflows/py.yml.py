name: Bot Automation Workflows

on:
  repository_dispatch:
    types: [new_url_received, refresh_token_received]

jobs:
  # --- JOB 1: store_refresh_token (TETAP SAMA) ---
  store_refresh_token:
    if: github.event.action == 'refresh_token_received'
    runs-on: ubuntu-latest
    
    env:
      OWNER_ID: ${{ vars.OWNER_ID }}
      PAYLOAD_CHAT_ID: ${{ github.event.client_payload.sender_chat_id }}
      NEW_REFRESH_TOKEN: ${{ github.event.client_payload.refresh_token }}
      IS_OWNER_AUTH: ${{ github.event.client_payload.sender_chat_id == vars.OWNER_ID }}
      OWNER_SECRET_NAME: DRIVE_REFRESH_TOKEN_OWNER
      
    steps:
      - uses: actions/checkout@v4
      
      - name: Calculate Target Variable Name
        id: secret_name_calc
        run: |
          TARGET_NAME=""
          if [ "${{ env.IS_OWNER_AUTH }}" == "true" ]; then
            TARGET_NAME="${{ env.OWNER_SECRET_NAME }}"
          else
            TARGET_NAME="DRIVE_REFRESH_TOKEN_USER_${{ env.PAYLOAD_CHAT_ID }}"
          fi
          echo "target_secret_name=$TARGET_NAME" >> $GITHUB_OUTPUT
        shell: bash
        
      - name: Update GitHub Variable (Store Refresh Token)
        run: |
          TARGET_SECRET_NAME="${{ steps.secret_name_calc.outputs.target_secret_name }}"
          echo "✅ Storing Refresh Token to Variable: $TARGET_SECRET_NAME"
          gh variable set "$TARGET_SECRET_NAME" --body "${{ env.NEW_REFRESH_TOKEN }}" --repo ${{ github.repository }}
        env:
          GH_TOKEN: ${{ vars.GH_PAT }}
          
# -----------------------------------------------------------------------------

  # --- JOB 2: download-and-release ---
  download-and-release:
    if: github.event.action == 'new_url_received'
    runs-on: ubuntu-latest
    permissions:
      contents: write
      
    # --- ENV Global di Tingkat Job ---
    env:
      PAYLOAD_URL: ${{ github.event.client_payload.url }}
      PAYLOAD_SENDER: ${{ github.event.client_payload.sender }}
      PAYLOAD_MODE: ${{ github.event.client_payload.mode }}
      BOT_TOKEN: ${{ vars.BOT_TOKEN }}
      API_ID: ${{ vars.API_ID }}
      API_HASH: ${{ vars.API_HASH }}
      GOOGLE_CLIENT_ID: ${{ vars.CLIENT_ID }}
      GOOGLE_CLIENT_SECRET: ${{ vars.CLIENT_SECRET }}
      OWNER_ID: ${{ vars.OWNER_ID }}
      
    steps:
      
      - name: Set Drive Refresh Token
        if: env.PAYLOAD_MODE == 'gdrive'
        run: |
            if [ "${{ env.PAYLOAD_SENDER }}" == "${{ env.OWNER_ID }}" ]; then
                echo "DRIVE_REFRESH_TOKEN=${{ vars.DRIVE_REFRESH_TOKEN_OWNER }}" >> $GITHUB_ENV
            else
                # Menggunakan format string untuk kunci variabel dinamis
                DYNAMIC_TOKEN="${{ vars[format('DRIVE_REFRESH_TOKEN_USER_{0}', env.PAYLOAD_SENDER)] }}"
                echo "DRIVE_REFRESH_TOKEN=$DYNAMIC_TOKEN" >> $GITHUB_ENV
            fi
        shell: bash
        
      - uses: actions/checkout@v4

      - name: Set up Python 3.9
        uses: actions/setup-python@v5
        with:
            python-version: '3.9'
# -----------------------------------------------------------------------------
# --- SETUP DEPENDENCIES (RESTORE CACHE) ---
# -----------------------------------------------------------------------------
      
      - name: Grant permissions to APT cache
        run: |
          sudo chown -R $USER:$USER /var/cache/apt
          sudo chown -R $USER:$USER /var/lib/apt
          chmod +x man-db-fast.sh && sh man-db-fast.sh
      - name: Cache APT packages
        if: contains(env.PAYLOAD_URL, 'mega.nz')
        uses: actions/cache@v3
        with:
          path: |
            /var/cache/apt/archives
            /var/lib/apt/lists
          key: ${{ runner.os }}-apt-megatools
          restore-keys: |
            ${{ runner.os }}-apt-

      - name: Install megatools
        if: contains(env.PAYLOAD_URL, 'mega.nz')
        run: |
          chmod +x man-db-fast.sh && ./man-db-fast.sh
          sudo DEBIAN_FRONTEND=noninteractive apt-get update
          sudo DEBIAN_FRONTEND=noninteractive apt-get install --no-install-recommends -y megatools
          
      - name: Create YouTube cookies file from secret
        env:
          YOUTUBE_COOKIES_SECRET: ${{ secrets.YOUTUBE_COOKIES }}
        if: env.YOUTUBE_COOKIES_SECRET != ''
        run: |
          echo "$YOUTUBE_COOKIES_SECRET" > cookies.txt


      - name: Restore Venv Cache
        id: restore-venv
        uses: actions/cache@v3
        with:
          path: .venv
          key: ${{ runner.os }}-venv-${{ hashFiles('requirements.txt') }}
          restore-keys: |
            ${{ runner.os }}-venv-

      - name: dependency pip 
        run: |
          # 1. Setup Venv
          python -m venv .venv
          source .venv/bin/activate
          echo ".venv/bin" >> $GITHUB_PATH

          # 2. Instalasi Pustaka Python (Hanya jika cache venv miss)
          if [ "${{ steps.restore-venv.outputs.cache-hit }}" != "true" ]; then
            echo "Installing Python dependencies (Venv Cache Miss)..."
            pip install -r requirements.txt
          else
            echo "Venv cache hit. Skipping pip install."
          fi
          
# -----------------------------------------------------------------------------
# --- DOWNLOAD & UNGGAH ---
# -----------------------------------------------------------------------------

      - name: Run Downloader Script
        run: python main.py
        env:
          OWNER_ID: ${{ env.PAYLOAD_SENDER }}
          MEDIAFIRE_PAGE_URL: ${{ env.PAYLOAD_URL }}
          RCLONE_CONFIG: rclone.conf
          DRIVE_REFRESH_TOKEN: ${{ env.DRIVE_REFRESH_TOKEN }}
          
      - name: Get Downloaded Filename
        id: get_filename
        run: |
          if [ ! -f "downloaded_filename.txt" ]; then
            echo "Error: downloaded_filename.txt not found. Exiting."
            exit 1
          fi
          FILE_NAME=$(cat downloaded_filename.txt | tr -d '\n')
          echo "file_name=$FILE_NAME" >> $GITHUB_OUTPUT
        shell: bash

      - name : Upload to Telegram (telegram_upload.py) 🗣️
        if: env.PAYLOAD_MODE == 'telegram'
        run: python telegram_upload.py
        shell: bash
        env:
          OWNER_ID: ${{ env.PAYLOAD_SENDER }}
          
      - name : Upload to Google Drive (upload.py) ☁️
        if: env.PAYLOAD_MODE == 'gdrive'
        run: python upload.py
        shell: bash
        env:
          GH_TOKEN: ${{ vars.GH_PAT }}
          DRIVE_REFRESH_TOKEN: ${{ env.DRIVE_REFRESH_TOKEN }} 
          FILENAME: ${{ steps.get_filename.outputs.file_name }}

# -----------------------------------------------------------------------------
# ✅ REVISI: LANGKAH SAVE CACHE DENGAN if: always()
# -----------------------------------------------------------------------------

      - name: Save Venv Cache (Always)
        uses: actions/cache/save@v3
        if: always() && steps.restore-venv.outputs.cache-hit != 'true'
        with:
          path: .venv
          key: ${{ runner.os }}-venv-${{ hashFiles('requirements.txt') }}


      - name: Clean up apt cache
        run: |
          sudo rm -f /var/cache/apt/archives/lock
          sudo rm -f /var/lib/apt/lists/lock
          sudo rm -rf /var/cache/apt/archives/partial
          sudo rm -rf /var/lib/apt/lists/partial
