KnulliTube (protótipo)

TESTE NO PC
  pip install pygame
  KT_MOCK=1 python3 main.py     # resultados falsos, sem internet
  (setas, Enter=A, Esc=B, X, Y, Espaço=START)

NO KNULLI
  1. Copie a pasta "knullitube" e o arquivo YouTube.sh para /userdata/roms/ports/
  2. (opcional) coloque o binário do yt-dlp em knullitube/bin/yt-dlp
  3. Atualize a lista de jogos no EmulationStation e abra "YouTube" em Ports
  4. Se algum botão estiver trocado, veja log.txt e ajuste Input.BTNS em main.py

REQUISITOS NO APARELHO: python3 + pygame, mpv, yt-dlp (veja via SSH: which mpv yt-dlp)
