name: Bot Automation Workflows

on:
  repository_dispatch:
    types: [new_url_received]

jobs:
  download-and-release:
    runs-on: ubuntu-latest
    
    permissions:
      contents: write

    env:
      PAYLOAD_URL: ${{ github.event.client_payload.url }}
      BOT_TOKEN: ${{ github.event.client_payload.bot_token }}
      OWNER_ID: ${{ github.event.client_payload.sender }}

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4

      # 1. RESTORE CACHE (Ambil folder modules & browser)
      - name: Restore Node & Playwright Cache
        uses: actions/cache/restore@v4
        with:
          path: |
            node_modules
            ~/.cache/ms-playwright
          key: ${{ runner.os }}-node-v5-${{ hashFiles('package.json') }}
          restore-keys: |
            ${{ runner.os }}-node-v5-

      # 2. INSTALLATION (Akan sangat cepat jika cache hit)
      - name: Install Dependencies
        run: |
          npm install
          # Install browser tanpa --with-deps (biasanya lib dasar sudah ada di ubuntu-latest)
          npx playwright install chromium
          
          # Install megatools & aria2 hanya jika dibutuhkan (kondisional)
          if [[ "${{ env.PAYLOAD_URL }}" == *"mega.nz"* || "${{ env.PAYLOAD_URL }}" == *"pixeldrain"* ]]; then
            sudo apt-get update && sudo apt-get install -y megatools aria2
          fi

      # 3. EXECUTION
      - name: Run Downloader
        run: |
          # Install xvfb secara instan untuk headles mode browser
          sudo apt-get install -y xvfb
          xvfb-run --auto-servernum node main.js
        env:
          PAYLOAD_URL: ${{ env.PAYLOAD_URL }}
          BOT_TOKEN: ${{ env.BOT_TOKEN }}
          OWNER_ID: ${{ env.OWNER_ID }}

      # 4. SAVE CACHE (Selalu simpan meski error/cancel)
      - name: Save Node & Playwright Cache
        if: always()
        uses: actions/cache/save@v4
        with:
          path: |
            node_modules
            ~/.cache/ms-playwright
          key: ${{ runner.os }}-node-v5-${{ hashFiles('package.json') }}
