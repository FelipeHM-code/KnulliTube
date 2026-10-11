#!/usr/bin/env python3
"""
KnulliTube - protótipo de player de YouTube para Knulli (pygame + yt-dlp + mpv)

Telas: Início, Pesquisa (teclado na tela), Resultados (com miniaturas),
Downloads (offline) e Configurações.

Teste no PC:   KT_MOCK=1 python3 main.py     (resultados falsos, sem internet)
No aparelho:   ./YouTube.sh                  (tela cheia)
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import pygame

BASE = os.path.dirname(os.path.abspath(__file__))
DL_DIR = os.path.join(BASE, "downloads")
CFG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "log.txt")
MOCK = os.environ.get("KT_MOCK") == "1"
W, H = 640, 480

# Cores
BG = (18, 18, 18)
PANEL = (32, 32, 32)
SEL = (62, 62, 62)
RED = (230, 33, 23)
WHITE = (240, 240, 240)
GRAY = (160, 160, 160)
DARK = (90, 90, 90)

QUALITIES = [360, 480, 720, 1080]
A_ACTIONS = ["play", "download"]
A_LABEL = {"play": "Reproduzir", "download": "Baixar"}


# ----------------------------------------------------------------- utilidades
def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + str(msg) + "\n")
    except OSError:
        pass


def load_cfg():
    cfg = {"quality": 720, "a_action": "play"}
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except OSError as e:
        log(f"erro ao salvar config: {e}")


def ytdlp_cmd():
    """Prefere ./bin/yt-dlp, depois o do sistema, depois o módulo Python."""
    local = os.path.join(BASE, "bin", "yt-dlp")
    if os.path.exists(local):
        return [local]
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    return [sys.executable, "-m", "yt_dlp"]


def sub_env():
    env = dict(os.environ)
    env["PATH"] = os.path.join(BASE, "bin") + os.pathsep + env.get("PATH", "")
    return env


def fmt_selector(q):
    # H.264 (avc1) primeiro: a maioria desses aparelhos não decodifica VP9/AV1 bem.
    return (f"bv*[vcodec^=avc1][height<={q}]+ba[ext=m4a]"
            f"/b[vcodec^=avc1][height<={q}]/b[height<={q}]")


def fmt_duration(sec):
    if not sec:
        return "AO VIVO"
    sec = int(sec)
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def online():
    try:
        socket.create_connection(("1.1.1.1", 53), 2).close()
        return True
    except OSError:
        return False


def wrap(text, font, width, maxlines):
    words = text.split()
    lines, cur, used = [], "", 0
    for w in words:
        t = (cur + " " + w).strip()
        if font.size(t)[0] <= width:
            cur = t
        else:
            if cur:
                lines.append(cur)
                used += 1
            cur = w
            if len(lines) == maxlines:
                break
    if len(lines) < maxlines and cur:
        lines.append(cur)
    # reticências se cortou
    if len(" ".join(lines).split()) < len(words) and lines:
        last = lines[-1]
        while last and font.size(last + "...")[0] > width:
            last = last[:-1]
        lines[-1] = last + "..."
    return lines


# ---------------------------------------------------------------------- input
class Input:
    KEYS = {
        pygame.K_UP: "up", pygame.K_DOWN: "down",
        pygame.K_LEFT: "left", pygame.K_RIGHT: "right",
        pygame.K_RETURN: "a", pygame.K_z: "a",
        pygame.K_ESCAPE: "b", pygame.K_BACKSPACE: "b",
        pygame.K_x: "x", pygame.K_y: "y", pygame.K_SPACE: "start",
    }
    # Mapeamento de botões do joystick. AJUSTE conforme o seu aparelho
    # (rode o app e veja o log.txt: cada botão pressionado é registrado).
    BTNS = {0: "a", 1: "b", 2: "x", 3: "y", 4: "l", 5: "r", 6: "select", 7: "start"}

    def __init__(self):
        self.held = None
        self.next_t = 0.0
        self.axes = {0: 0.0, 1: 0.0}

    def _hold(self, d, out):
        if d != self.held:
            self.held = d
            self.next_t = time.time() + 0.4
            if d:
                out.append(d)

    def translate(self, e):
        out = []
        if e.type == pygame.KEYDOWN:
            a = self.KEYS.get(e.key)
            if a:
                out.append(a)
        elif e.type == pygame.JOYBUTTONDOWN:
            log(f"botão {e.button}")
            a = self.BTNS.get(e.button)
            if a:
                out.append(a)
        elif e.type == pygame.JOYHATMOTION:
            x, y = e.value
            d = ("left" if x < 0 else "right" if x > 0 else
                 "up" if y > 0 else "down" if y < 0 else None)
            self._hold(d, out)
        elif e.type == pygame.JOYAXISMOTION and e.axis in (0, 1):
            self.axes[e.axis] = e.value
            ax, ay = self.axes[0], self.axes[1]
            d = None
            if abs(ay) > abs(ax) and abs(ay) > 0.6:
                d = "down" if ay > 0 else "up"
            elif abs(ax) > 0.6:
                d = "right" if ax > 0 else "left"
            self._hold(d, out)
        return out

    def tick(self):
        if self.held and time.time() >= self.next_t:
            self.next_t = time.time() + 0.08
            return [self.held]
        return []


# ---------------------------------------------------------------------- telas
class Menu:
    """Controla índice e rolagem de uma lista."""

    def __init__(self, visible):
        self.idx = 0
        self.top = 0
        self.visible = visible

    def move(self, delta, total):
        if total == 0:
            self.idx = self.top = 0
            return
        self.idx = max(0, min(total - 1, self.idx + delta))
        if self.idx < self.top:
            self.top = self.idx
        elif self.idx >= self.top + self.visible:
            self.top = self.idx - self.visible + 1


class Screen:
    title = ""
    hints = ""

    def __init__(self, app):
        self.app = app

    def handle(self, a):
        pass

    def draw(self, s):
        pass


class HomeScreen(Screen):
    title = "KnulliTube"
    hints = "A: abrir"

    def __init__(self, app):
        super().__init__(app)
        self.items = ["Pesquisar", "Downloads", "Configurações", "Sair"]
        self.menu = Menu(6)

    def handle(self, a):
        if a == "up":
            self.menu.move(-1, len(self.items))
        elif a == "down":
            self.menu.move(1, len(self.items))
        elif a == "a":
            i = self.menu.idx
            if i == 0:
                self.app.push(SearchScreen(self.app))
            elif i == 1:
                self.app.push(DownloadsScreen(self.app))
            elif i == 2:
                self.app.push(SettingsScreen(self.app))
            else:
                self.app.running = False

    def draw(self, s):
        app = self.app
        # logo
        pygame.draw.rect(s, RED, (205, 70, 64, 44), border_radius=12)
        pygame.draw.polygon(s, WHITE, [(229, 82), (229, 102), (247, 92)])
        app.text(s, "KnulliTube", 280, 76, size=40)
        for n, label in enumerate(self.items):
            y = 160 + n * 52
            r = pygame.Rect(170, y, 300, 44)
            if n == self.menu.idx:
                pygame.draw.rect(s, SEL, r, border_radius=8)
                pygame.draw.rect(s, RED, (r.x, r.y, 5, r.h), border_radius=3)
            app.text(s, label, r.x + 22, r.y + 10, size=26)


class SearchScreen(Screen):
    title = "Pesquisar"
    hints = "A: digitar   B: apagar   Y: espaço   START: buscar"
    ROWS = [list("ABCDEFGHIJ"), list("KLMNOPQRST"), list("UVWXYZ0123"),
            list("456789.-'"), ["ESPAÇO", "APAGAR", "BUSCAR"]]

    def __init__(self, app):
        super().__init__(app)
        self.q = ""
        self.r = 0
        self.c = 0

    def submit(self):
        if self.q.strip():
            self.app.push(ResultsScreen(self.app, self.q.strip()))

    def press(self):
        key = self.ROWS[self.r][self.c]
        if key == "ESPAÇO":
            self.q += " "
        elif key == "APAGAR":
            self.q = self.q[:-1]
        elif key == "BUSCAR":
            self.submit()
        elif len(self.q) < 60:
            self.q += key.lower()

    def handle(self, a):
        row = self.ROWS[self.r]
        if a == "left":
            self.c = (self.c - 1) % len(row)
        elif a == "right":
            self.c = (self.c + 1) % len(row)
        elif a in ("up", "down"):
            self.r = (self.r + (1 if a == "down" else -1)) % len(self.ROWS)
            self.c = min(self.c, len(self.ROWS[self.r]) - 1)
        elif a == "a":
            self.press()
        elif a == "b":
            if self.q:
                self.q = self.q[:-1]
            else:
                self.app.pop()
        elif a == "y":
            self.q += " "
        elif a == "start":
            self.submit()

    def draw(self, s):
        app = self.app
        box = pygame.Rect(30, 62, W - 60, 46)
        pygame.draw.rect(s, PANEL, box, border_radius=8)
        shown = self.q + ("_" if int(time.time() * 2) % 2 == 0 else " ")
        app.text(s, shown, box.x + 14, box.y + 9, size=28)
        for ri, row in enumerate(self.ROWS):
            n = len(row)
            special = ri == len(self.ROWS) - 1
            cw = (W - 60) // (n if special else 10)
            for ci, key in enumerate(row):
                r = pygame.Rect(30 + ci * cw, 130 + ri * 56, cw - 6, 48)
                on = (ri == self.r and ci == self.c)
                pygame.draw.rect(s, RED if on else PANEL, r, border_radius=8)
                app.text(s, key, r.centerx, r.centery, size=24 if not special else 20,
                         center=True)


class ResultsScreen(Screen):
    hints = "A: abrir   X/START: baixar   B: voltar"
    ROW_H = 82

    def __init__(self, app, query):
        super().__init__(app)
        self.title = f"Resultados: {query}"
        self.query = query
        self.items = []
        self.state = "loading"
        self.error = ""
        self.menu = Menu(5)
        threading.Thread(target=self._search, daemon=True).start()

    def _search(self):
        try:
            if MOCK:
                time.sleep(0.6)
                self.items = [
                    {"id": f"mock{i}", "title": f"{self.query.title()} - vídeo de exemplo "
                     f"número {i} com um título bem comprido para testar quebra de linha",
                     "channel": f"Canal {i}", "duration": 120 + i * 337}
                    for i in range(1, 11)]
            else:
                out = subprocess.run(
                    ytdlp_cmd() + [f"ytsearch12:{self.query}", "--flat-playlist",
                                   "-J", "--no-warnings"],
                    capture_output=True, text=True, timeout=40, env=sub_env())
                if out.returncode != 0:
                    raise RuntimeError(out.stderr.strip().splitlines()[-1]
                                       if out.stderr.strip() else "yt-dlp falhou")
                data = json.loads(out.stdout)
                self.items = [{"id": e["id"], "title": e.get("title") or "(sem título)",
                               "channel": e.get("channel") or e.get("uploader") or "",
                               "duration": e.get("duration")}
                              for e in data.get("entries", []) if e and e.get("id")]
            self.state = "ok" if self.items else "empty"
        except Exception as ex:  # noqa: BLE001
            log(f"busca falhou: {ex}")
            self.error = str(ex)[:120]
            self.state = "error"

    def handle(self, a):
        if a == "b":
            self.app.pop()
        elif self.state != "ok":
            return
        elif a == "up":
            self.menu.move(-1, len(self.items))
        elif a == "down":
            self.menu.move(1, len(self.items))
        elif a == "left":
            self.menu.move(-5, len(self.items))
        elif a == "right":
            self.menu.move(5, len(self.items))
        elif a == "a":
            item = self.items[self.menu.idx]
            if self.app.cfg["a_action"] == "download":
                self.app.start_download(item)
            else:
                self.app.play_url(item)
        elif a in ("x", "start"):
            self.app.start_download(self.items[self.menu.idx])

    def draw(self, s):
        app = self.app
        if self.state == "loading":
            app.text(s, "Buscando" + "." * (int(time.time() * 3) % 4), W // 2, 240,
                     size=28, center=True)
            return
        if self.state in ("error", "empty"):
            msg = "Nada encontrado" if self.state == "empty" else "Erro na busca"
            app.text(s, msg, W // 2, 210, size=28, center=True)
            if self.error:
                for i, ln in enumerate(wrap(self.error, app.f_small, W - 80, 3)):
                    app.text(s, ln, W // 2, 250 + i * 22, size=18, color=GRAY, center=True)
            return
        for n in range(self.menu.top, min(len(self.items), self.menu.top + 5)):
            it = self.items[n]
            y = 48 + (n - self.menu.top) * self.ROW_H
            r = pygame.Rect(8, y, W - 16, self.ROW_H - 4)
            if n == self.menu.idx:
                pygame.draw.rect(s, SEL, r, border_radius=8)
            th = app.thumb(it["id"])
            tr = pygame.Rect(r.x + 6, r.y + 5, 128, 72)
            if th:
                s.blit(th, tr)
            else:
                pygame.draw.rect(s, PANEL, tr)
                pygame.draw.polygon(s, DARK, [(tr.x + 54, tr.y + 24),
                                              (tr.x + 54, tr.y + 48), (tr.x + 76, tr.y + 36)])
            dur = fmt_duration(it["duration"])
            dw = app.f_small.size(dur)[0] + 8
            pygame.draw.rect(s, (0, 0, 0), (tr.right - dw - 2, tr.bottom - 20, dw, 18),
                             border_radius=3)
            app.text(s, dur, tr.right - dw + 2, tr.bottom - 19, size=16)
            for i, ln in enumerate(wrap(it["title"], app.f_body, r.w - 160, 2)):
                app.text(s, ln, r.x + 146, r.y + 6 + i * 24, size=22)
            sub = it["channel"]
            if it["id"] in app.downloads:
                sub = f"Baixando {app.downloads[it['id']]:.0f}%"
            app.text(s, sub, r.x + 146, r.y + 56, size=18, color=RED if it["id"] in app.downloads else GRAY)


class DownloadsScreen(Screen):
    title = "Downloads"
    hints = "A: reproduzir   X: apagar   B: voltar"

    def __init__(self, app):
        super().__init__(app)
        self.menu = Menu(7)
        self.files = []
        self.confirm = None
        self.refresh()

    def refresh(self):
        os.makedirs(DL_DIR, exist_ok=True)
        fs = [f for f in os.listdir(DL_DIR) if f.lower().endswith((".mp4", ".mkv", ".webm"))]
        fs.sort(key=lambda f: os.path.getmtime(os.path.join(DL_DIR, f)), reverse=True)
        self.files = fs
        self.menu.move(0, len(fs))

    def handle(self, a):
        if a == "b":
            self.app.pop()
        elif a == "up":
            self.menu.move(-1, len(self.files))
        elif a == "down":
            self.menu.move(1, len(self.files))
        elif a == "a" and self.files:
            self.app.play_file(os.path.join(DL_DIR, self.files[self.menu.idx]))
        elif a == "x" and self.files:
            f = self.files[self.menu.idx]
            if self.confirm == f and time.time() < self.confirm_until:
                try:
                    os.remove(os.path.join(DL_DIR, f))
                    self.app.toast("Vídeo apagado")
                except OSError as e:
                    self.app.toast(f"Erro: {e}")
                self.confirm = None
                self.refresh()
            else:
                self.confirm = f
                self.confirm_until = time.time() + 3
                self.app.toast("Aperte X de novo para apagar")

    def draw(self, s):
        app = self.app
        if not self.files:
            app.text(s, "Nenhum vídeo baixado ainda", W // 2, 230, size=26, color=GRAY, center=True)
            return
        for n in range(self.menu.top, min(len(self.files), self.menu.top + 7)):
            y = 52 + (n - self.menu.top) * 58
            r = pygame.Rect(8, y, W - 16, 54)
            if n == self.menu.idx:
                pygame.draw.rect(s, SEL, r, border_radius=8)
            name = os.path.splitext(self.files[n])[0]
            for i, ln in enumerate(wrap(name, app.f_body, r.w - 24, 2)):
                app.text(s, ln, r.x + 12, r.y + 4 + i * 24, size=22)


class SettingsScreen(Screen):
    title = "Configurações"
    hints = "←/→: alterar   A: executar   B: voltar"

    def __init__(self, app):
        super().__init__(app)
        self.menu = Menu(6)
        self.n_items = 3
        self.ver = "..."
        threading.Thread(target=self._version, daemon=True).start()

    def _version(self):
        try:
            if MOCK:
                self.ver = "mock"
                return
            out = subprocess.run(ytdlp_cmd() + ["--version"], capture_output=True,
                                 text=True, timeout=15, env=sub_env())
            self.ver = out.stdout.strip() or "?"
        except Exception:  # noqa: BLE001
            self.ver = "não encontrado"

    def handle(self, a):
        cfg = self.app.cfg
        if a == "b":
            self.app.pop()
        elif a == "up":
            self.menu.move(-1, self.n_items)
        elif a == "down":
            self.menu.move(1, self.n_items)
        elif a in ("left", "right", "a"):
            d = -1 if a == "left" else 1
            if self.menu.idx == 0 and a != "a":
                i = QUALITIES.index(cfg["quality"]) if cfg["quality"] in QUALITIES else 2
                cfg["quality"] = QUALITIES[max(0, min(len(QUALITIES) - 1, i + d))]
                save_cfg(cfg)
            elif self.menu.idx == 1:
                cfg["a_action"] = A_ACTIONS[(A_ACTIONS.index(cfg["a_action"]) + 1) % 2]
                save_cfg(cfg)
            elif self.menu.idx == 2 and a == "a":
                self.app.update_ytdlp()

    def draw(self, s):
        app = self.app
        rows = [("Qualidade", f"{app.cfg['quality']}p"),
                ("Botão A na lista", A_LABEL[app.cfg["a_action"]]),
                ("Atualizar yt-dlp", f"versão {self.ver}")]
        for n, (k, v) in enumerate(rows):
            r = pygame.Rect(30, 70 + n * 60, W - 60, 52)
            if n == self.menu.idx:
                pygame.draw.rect(s, SEL, r, border_radius=8)
            app.text(s, k, r.x + 16, r.y + 13, size=24)
            app.text(s, v, r.right - 16 - app.f_body.size(v)[0], r.y + 13, size=24, color=GRAY)


# ------------------------------------------------------------------------ app
class App:
    def __init__(self):
        pygame.init()
        pygame.joystick.init()
        self.joys = []
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i)
            j.init()
            self.joys.append(j)
        pygame.key.set_repeat(350, 70)
        pygame.mouse.set_visible(False)
        self.open_display()
        self.f_title = pygame.font.Font(None, 30)
        self.f_body = pygame.font.Font(None, 26)
        self.f_small = pygame.font.Font(None, 20)
        self._fonts = {}
        self.cfg = load_cfg()
        self.input = Input()
        self.running = True
        self.stack = [HomeScreen(self)]
        self.toast_text = ""
        self.toast_until = 0
        self.thumbs = {}
        self.thumbs_loading = set()
        self.downloads = {}  # id -> porcentagem
        self._mpv_gamepad = None
        os.makedirs(DL_DIR, exist_ok=True)
        if not MOCK and not online():
            self.toast("Sem internet: abrindo Downloads")
            self.push(DownloadsScreen(self))

    # ---- display / texto
    def open_display(self):
        flags = pygame.FULLSCREEN | pygame.SCALED if os.environ.get("KT_FULLSCREEN") == "1" else 0
        pygame.display.init()
        self.screen = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("KnulliTube")

    def font(self, size):
        if size not in self._fonts:
            self._fonts[size] = pygame.font.Font(None, size)
        return self._fonts[size]

    def text(self, s, txt, x, y, size=22, color=WHITE, center=False):
        img = self.font(size + 4).render(txt, True, color)
        r = img.get_rect()
        if center:
            r.center = (x, y)
        else:
            r.topleft = (x, y)
        s.blit(img, r)

    def toast(self, msg, secs=3):
        self.toast_text = msg
        self.toast_until = time.time() + secs

    # ---- navegação
    def push(self, scr):
        self.stack.append(scr)

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()
        else:
            self.running = False

    # ---- miniaturas
    def thumb(self, vid):
        if vid in self.thumbs:
            return self.thumbs[vid]
        if vid not in self.thumbs_loading and not MOCK:
            self.thumbs_loading.add(vid)
            threading.Thread(target=self._load_thumb, args=(vid,), daemon=True).start()
        return None

    def _load_thumb(self, vid):
        try:
            url = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
            with urllib.request.urlopen(url, timeout=10) as r:
                data = r.read()
            img = pygame.image.load(io.BytesIO(data))
            self.thumbs[vid] = pygame.transform.smoothscale(img, (128, 72))
        except Exception as e:  # noqa: BLE001
            log(f"miniatura {vid}: {e}")
            self.thumbs[vid] = None

    # ---- reprodução
    def _mpv_has_gamepad(self, mpv):
        if self._mpv_gamepad is None:
            try:
                out = subprocess.run([mpv, "--list-options"], capture_output=True,
                                     text=True, timeout=10).stdout
                self._mpv_gamepad = "--input-gamepad" in out
            except Exception:  # noqa: BLE001
                self._mpv_gamepad = False
        return self._mpv_gamepad

    def _run_mpv(self, target, is_url):
        mpv = shutil.which("mpv")
        if not mpv:
            self.toast("mpv não encontrado neste aparelho", 5)
            return
        cmd = [mpv, "--fs", "--really-quiet", "--hwdec=auto-safe"]
        if self._mpv_has_gamepad(mpv):
            cmd.append("--input-gamepad=yes")  # A = pausa, B = sair (padrão do mpv)
        if is_url:
            cmd.append(f"--ytdl-format={fmt_selector(self.cfg['quality'])}")
            if ytdlp_cmd()[0] != sys.executable:
                cmd.append(f"--script-opts=ytdl_hook-ytdl_path={ytdlp_cmd()[0]}")
        cmd.append(target)
        log("mpv: " + " ".join(cmd))
        self.toast("Carregando vídeo...", 2)
        self.draw()
        pygame.display.flip()
        pygame.display.quit()  # libera a tela para o mpv
        try:
            subprocess.run(cmd, env=sub_env())
        finally:
            self.open_display()

    def play_url(self, item):
        self._run_mpv(f"https://www.youtube.com/watch?v={item['id']}", True)

    def play_file(self, path):
        self._run_mpv(path, False)

    # ---- downloads
    def start_download(self, item):
        vid = item["id"]
        if vid in self.downloads:
            self.toast("Já está baixando")
            return
        if MOCK:
            self.toast("(mock) download iniciado")
            return
        self.downloads[vid] = 0.0
        self.toast("Download iniciado")
        threading.Thread(target=self._download, args=(item,), daemon=True).start()

    def _download(self, item):
        vid = item["id"]
        q = self.cfg["quality"]
        cmd = ytdlp_cmd() + [
            f"https://www.youtube.com/watch?v={vid}", "-f", fmt_selector(q),
            "--merge-output-format", "mp4", "--newline", "--no-playlist",
            "-o", os.path.join(DL_DIR, "%(title).80B [%(id)s].%(ext)s")]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, env=sub_env())
            for line in p.stdout:
                if "[download]" in line and "%" in line:
                    try:
                        self.downloads[vid] = float(line.split("%")[0].split()[-1])
                    except ValueError:
                        pass
            p.wait()
            ok = p.returncode == 0
        except Exception as e:  # noqa: BLE001
            log(f"download falhou: {e}")
            ok = False
        self.downloads.pop(vid, None)
        self.toast("Download concluído" if ok else "Falha no download", 4)

    def update_ytdlp(self):
        self.toast("Atualizando yt-dlp...", 30)

        def work():
            try:
                out = subprocess.run(ytdlp_cmd() + ["-U"], capture_output=True, text=True,
                                     timeout=120, env=sub_env())
                msg = (out.stdout.strip().splitlines() or ["ok"])[-1]
                if out.returncode != 0:  # instalado via pip
                    out = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                                         capture_output=True, text=True, timeout=180)
                    msg = (out.stdout.strip().splitlines() or ["falhou"])[-1]
                self.toast(msg[:60], 5)
            except Exception as e:  # noqa: BLE001
                log(f"update falhou: {e}")
                self.toast("Falha ao atualizar", 5)

        threading.Thread(target=work, daemon=True).start()

    # ---- loop
    def handle(self, a):
        if self.stack:
            self.stack[-1].handle(a)

    def draw(self):
        s = self.screen
        s.fill(BG)
        scr = self.stack[-1]
        # cabeçalho
        pygame.draw.rect(s, PANEL, (0, 0, W, 44))
        pygame.draw.rect(s, RED, (10, 11, 26, 20), border_radius=5)
        pygame.draw.polygon(s, WHITE, [(19, 15), (19, 27), (28, 21)])
        self.text(s, scr.title[:44], 46, 11, size=26)
        scr.draw(s)
        # rodapé
        pygame.draw.rect(s, PANEL, (0, H - 30, W, 30))
        self.text(s, scr.hints, 10, H - 24, size=16, color=GRAY)
        if self.downloads:
            pct = sum(self.downloads.values()) / len(self.downloads)
            pygame.draw.rect(s, DARK, (W - 130, H - 20, 120, 8), border_radius=4)
            pygame.draw.rect(s, RED, (W - 130, H - 20, int(120 * pct / 100), 8), border_radius=4)
        # aviso temporário
        if time.time() < self.toast_until:
            w = min(W - 40, self.f_body.size(self.toast_text)[0] + 30)
            r = pygame.Rect((W - w) // 2, H - 80, w, 36)
            pygame.draw.rect(s, (0, 0, 0), r, border_radius=10)
            pygame.draw.rect(s, RED, r, 2, border_radius=10)
            self.text(s, self.toast_text, r.centerx, r.centery, size=20, center=True)

    def run(self):
        clock = pygame.time.Clock()
        while self.running:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.running = False
                for a in self.input.translate(e):
                    self.handle(a)
            for a in self.input.tick():
                self.handle(a)
            self.draw()
            pygame.display.flip()
            clock.tick(30)
        pygame.quit()


if __name__ == "__main__":
    try:
        App().run()
    except Exception as exc:  # noqa: BLE001
        log(f"erro fatal: {exc!r}")
        raise
