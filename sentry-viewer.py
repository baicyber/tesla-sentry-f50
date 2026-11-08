#!/usr/bin/env python3
"""Tesla 哨兵视频查看器 - 单文件, 纯标准库, 无依赖
功能: 多路播放器(4路/6路自适应) / 时间线 / 皮肤上传 / 锁车语音上传(经归档同步写入车机U盘)
访问: http://<f50-ip>:8080
"""
import base64
import datetime
import json
import os
import re
import hmac
import hashlib
import math
import shutil
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import socket
import http.client
import ssl
import zipfile
import tempfile
import sys
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- 配置加载兜底 ----
# OpenWrt procd 不会把 init.d 脚本里 export 的变量传给服务进程 (实测 /proc/PID/environ 为空),
# 而 systemd 的 EnvironmentFile 会全量注入 —— 两边行为不一致会让"照 README 改 conf 不生效"。
# 在读取任何 TSF_* 之前, 把 conf 里尚未出现在环境变量中的项补进 os.environ。
# 优先级: 显式环境变量 > /etc/tesla-sentry.conf > 代码默认值; 空值按"用默认"处理。
def _load_conf(path="/etc/tesla-sentry.conf"):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k.startswith("TSF_") and v and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_conf()

# 路径/端口优先取环境变量 (缺失时已由 _load_conf 从 conf 补齐), 便于装到非 F50 设备上时改归档区、端口等
ARCHIVE = os.environ.get("TSF_ARCHIVE", "/archive")
SKIN_DIR = os.path.join(ARCHIVE, ".skins")
AUDIO_Q = os.path.join(ARCHIVE, "boombox-queue")
AUDIO_DONE = os.path.join(ARCHIVE, "boombox-done")
FILES_DIR = os.path.join(ARCHIVE, "files")
PORT = int(os.environ.get("TSF_PORT", "8080"))
CATS = ["SentryClips", "SavedClips", "RecentClips"]
# ===== 多语言表 (由 tools/i18n_*.json 生成, key 为中文原文) =====
LANG_NAMES = [("zh", "中文"), ("en", "English"), ("de", "Deutsch"), ("fr", "Français"),
              ("it", "Italiano"), ("es", "Español"), ("nl", "Nederlands"), ("ru", "Русский"),
              ("ja", "日本語"), ("ko", "한국어")]
LANG_TABLE = {"de":{"App ID — cli_ 开头":"App ID — beginnt mit cli_","App Secret (已保存则留空表示不修改)":"App Secret (leer lassen = unverändert)","‹ 返回":"‹ Zurück","⏳ 待写入车机U盘:":"⏳ Warten auf Schreiben auf den USB-Stick:","▶ 轻触开始同步播放":"▶ Tippen für synchrone Wiedergabe","⚙ 个性化":"⚙ Personalisieren","✅ 已写入:":"✅ Geschrieben:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"CSS-Skin-Datei oder Hintergrundbild hochladen (jpg/png/webp). Bilder im CSS über /skin-media/Dateiname einbinden.","下载":"Herunterladen","下载清晰度":"Download-Qualität","主题":"Design","也推手动保存":"Manuelle Speicherungen ebenfalls pushen","仅支持 wav/mp3":"Nur wav/mp3 unterstützt","仅限本人访问":"Nur für den Besitzer","今天":"Heute","位置":"Position","使用中":"Aktiv","侦测到物体移动":"Bewegung erkannt","侦测到碰撞/震动":"Aufprall/Erschütterung erkannt","保存失败":"Speichern fehlgeschlagen","保存设置":"Einstellungen speichern","修改密码":"Passwort ändern","全选":"Alle auswählen","全部":"Alle","全部日期":"Alle Daten","再输一次新密码":"Neues Passwort wiederholen","删除":"Löschen","刹车":"Bremse","刹车占比":"Bremsanteil","前视":"Vorne","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"Das Update lädt das neueste Programm von GitHub und mehreren Mirrors parallel, überschreibt lokale Dateien und startet den Dienst automatisch neu (ca. 5 s); Konfiguration, Aufnahmen und Anmeldedaten bleiben unberührt.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"Eine halbtransparente Zeile: Zeit/Position/Geschwindigkeit/Gang/Pedale/Bremse, verdeckt kaum","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"Einzelauswahl lädt die Originaldatei direkt herunter (mit Wasserzeichen lokal komponiert); Mehrfachauswahl fügt ein Multi-View-Video zusammen. Download/ZIP-Export in der gewählten Qualität (beim ersten Mal Wartezeit durch Transkodierung).","原因":"Grund","原画":"Original","发送测试":"Test senden","叠加行车信息水印":"Fahrdaten-Wasserzeichen einblenden","右B柱":"B-Säule rechts","右前翼":"Kotflügel vorn rechts","右翼子板":"Kotflügel rechts","合成视频":"Video zusammenfügen","同步状态":"Sync-Status","后视":"Hinten","启用推送":"Push aktivieren","哨兵告警":"Sentry-Alarme","哨兵模式触发":"Sentry-Modus ausgelöst","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"Bei Sentry-Auslösung werden Zeit/Position, Auslösegrund und Auslösebild an Feishu gepusht. Standardmäßig private Nachrichten über die eigene App (nur für dich sichtbar); alternativ Gruppen-Bot-webhook (für alle in der Gruppe sichtbar). Video-Push nur für private Nachrichten.","哨兵警觉":"Sentry-Wahrnehmung","回滚到上一版本":"Zur vorherigen Version zurückkehren","天":"Tage","实时同步中":"Live-Sync läuft","密码":"Passwort","尝试次数过多，请 5 分钟后再试":"Zu viele Versuche, bitte in 5 Minuten erneut versuchen","展开详细数据 ⌄":"Details ausklappen ⌄","左B柱":"B-Säule links","左前翼":"Kotflügel vorn links","左翼子板":"Kotflügel links","已保存":"Gespeichert","已删除":"Gelöscht","已加入队列, 归档同步时写入车机U盘":"In Warteschlange, wird bei der Archiv-Synchronisierung auf den Auto-USB-Stick geschrieben","已同步":"Synchronisiert","已是最新":"Aktuell","延迟推送":"Push-Verzögerung","当前密码":"Aktuelles Passwort","当前版本":"Aktuelle Version","恢复车机导出":"Auto-Zugriff wiederherstellen","手动保存":"Manuelle Speicherung","打包zip":"ZIP-Paket","按喇叭保存":"Hupen-Speicherung","换个日期或点「全部」查看本分类所有事件":"Anderes Datum wählen oder \"Alle\" tippen, um alle Ereignisse dieser Kategorie zu sehen","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"Empfänger: deine open_id (beginnt mit ou_) oder Feishu-Login-E-Mail","推送内容：封面卡片（触发那帧图 + 信息，最快）":"Push-Inhalt: Cover-Karte (Auslösebild + Infos, am schnellsten)","推送内容：触发摄像头视频（可回放）":"Push-Inhalt: Video der Auslösekamera (abspielbar)","摄像头":"Kamera","收起详细数据 ⌃":"Details einklappen ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"Nach der Änderung meldet sich dieses Gerät automatisch mit dem neuen Passwort neu an; Anmeldungen auf Handy/weiteren Geräten werden sofort ungültig.","敏感信息已明文显示，注意别外发":"Sensible Daten im Klartext, beim Weitergeben aufpassen","敏感信息已隐藏（截图安全）":"Sensible Daten verborgen (screenshot-sicher)","新密码 (至少 6 位)":"Neues Passwort (mindestens 6 Zeichen)","方向盘角度":"Lenkwinkel","日期":"Datum","时间":"Zeit","昨天":"Gestern","暂无归档视频":"Noch keine archivierten Videos","暂无皮肤, 使用默认外观":"Keine Skins, Standarddarstellung aktiv","最新版本":"Neueste Version","有新版本":"Neue Version verfügbar","未标注原因":"Kein Grund angegeben","未检查":"Nicht geprüft","条":"Clips","查看链接前缀 (可选): http://你的域名:端口":"Link-Präfix (optional): http://deine-domain:port","检查中…":"Prüfe…","检查更新":"Nach Updates suchen","正在上传…":"Wird hochgeladen…","正在下载并校验新版本…":"Neue Version wird heruntergeladen und geprüft…","正在合成多画面视频":"Multi-View-Video wird erstellt","活动ID":"Ereignis-ID","浅色":"Hell","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"Auto-USB-Stick durchsuchen und verwalten (das von Tesla geschriebene Abbild). Beim Verbinden wird das Lesen durch das Auto einige Sekunden unterbrochen; nach 5 Minuten ohne Bedienung erfolgt die Wiederherstellung automatisch.","深色":"Dunkel","清空":"Leeren","点击行车记录仪图标":"Dashcam-Symbol angetippt","片段":"Clips","用户名":"Benutzername","用户名或密码不对":"Benutzername oder Passwort falsch","画面":"Ansichten","界面语言，保存在本机浏览器":"Sprache der Oberfläche, im Browser gespeichert","登录":"Anmelden","登录状态保留":"Anmeldung gemerkt für","盒子归档存储":"Box-Archivspeicher","确定要退出登录吗？":"Wirklich abmelden?","私聊推送 — 只有我本人能看到":"Privatnachricht — nur ich kann sie sehen","秒 (等片段写完再推)":"Sekunden (warten, bis Clips fertig geschrieben sind)","秒（从触发前 5 秒开始截）":"Sekunden (beginnt 5 s vor Auslösung)","立即升级":"Jetzt aktualisieren","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"Gruppen-Bot-webhook (nur für Gruppen-Push): https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"Gruppen-Bot — für alle in der Gruppe sichtbar","自动":"Auto","行车记录":"Dashcam","行车记录仪菜单保存":"Speichern im Dashcam-Menü","视频时长":"Videolänge","视频清晰度：360p（约 1.8MB / 30 秒）":"Videoqualität: 360p (ca. 1,8MB / 30 s)","视频清晰度：480p（约 3MB / 30 秒）":"Videoqualität: 480p (ca. 3MB / 30 s)","视频清晰度：720p（约 5MB / 30 秒）":"Videoqualität: 720p (ca. 5MB / 30 s)","触发原因":"Auslösegrund","设为锁车音":"Als Verriegelungston festlegen","设备正在拼接各路画面, 请勿关闭页面":"Ansichten werden zusammengefügt, Seite nicht schließen","设置":"Einstellungen","该筛选下暂无事件":"Keine Ereignisse für diesen Filter","跳转":"Los","车机U盘未连接":"Auto-USB-Stick nicht verbunden","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"Nach der Aufnahme im Auto übernimmt die Archiv-Synchronisierung die Videos automatisch (Spiegelung benötigt 10 Min. Leerlauf)","车机要求单个音频 <1MB, 请裁剪后重试":"Das Auto verlangt Audio <1MB, bitte kürzen und erneut versuchen","车载U盘存储":"USB-Stick-Speicher","车门拉动":"Türzug","车门被拉动":"Tür bewegt","载入中…":"Wird geladen…","连接车机U盘":"Auto-USB-Stick verbinden","退出登录":"Abmelden","选择日期…":"Datum wählen…","选择要导出的画面":"Ansichten für den Export wählen","选择语言":"Sprache wählen","遭遇袭击":"Aufprall","遭遇袭击 (紧急报警)":"Aufprall (Notruf)","锁车音":"Verriegelungston","锁车音必须是 WAV (mp3 只能当喇叭音效)":"Verriegelungston muss WAV sein (mp3 nur als Boombox-Sound)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"Verriegelungston erfordert WAV 44.1kHz 16-bit <1MB 1–5 s; LockChime.wav kommt ins Stammverzeichnis des Auto-USB-Sticks, andere mp3/wav werden als Boombox-Sounds in den Ordner Boombox/ geschrieben. Dateien werden bei der Archiv-Synchronisierung auf den USB-Stick geschrieben.","门把手被拉动":"Türgriff gezogen","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ Datei in den aktuellen Ordner hochladen (beliebiger Typ, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ Skin-Datei hochladen (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ Audio hochladen (wav/mp3, <1MB)","🌐 语言":"🌐 Sprache","🎨 网页背景":"🎨 Skin","👁 显示":"👁 Anzeigen","📂 文件":"📂 Dateien","🔄 更新":"🔄 Update","🔊 锁车语音":"🔊 Verriegelungston","🔑 账号":"🔑 Konto","🔔 推送":"🔔 Push","🗄 归档区":"🗄 Archivbereich","🙈 隐藏":"🙈 Ausblenden","🚗 特斯拉哨兵查看器":"🚗 Tesla Sentry Viewer","🚗 车机U盘":"🚗 Auto-USB-Stick","归档":"Archiv","待确认":"Ausstehend","同步服务未运行":"Synchronisierungsdienst läuft nicht","路画面":"Kanalansicht","最近一次":"Letzter Lauf","未启用":"Nicht aktiviert","已启用私聊推送, 等下一个哨兵事件":"Direktnachrichten aktiviert, warte auf das nächste Sentry-Ereignis","已启用, 但接收人/应用凭据未配全":"Aktiviert, aber Empfänger-/App-Zugangsdaten unvollständig","读取设置失败":"Einstellungen konnten nicht geladen werden","私聊需要 App ID / Secret + 接收人":"Direktnachrichten benötigen App ID / Secret + Empfänger","请先填群机器人 webhook":"Bitte zuerst den Gruppenbot-Webhook eintragen","保存中…":"Speichern…","发送失败":"Senden fehlgeschlagen","正在发送测试卡片…":"Testkarte wird gesendet…","当前未开启登录保护, 设置密码后即开启":"Anmeldeschutz ist aus, mit einem Passwort wird er aktiviert","新密码至少 6 位":"Neues Passwort mindestens 6 Zeichen","两次输入的新密码不一致":"Die neuen Passwörter stimmen nicht überein","提交中…":"Wird übermittelt…","空目录":"Leerer Ordner","正在连接 GitHub…":"Verbindung zu GitHub…","检查失败":"Prüfung fehlgeschlagen","连不上 GitHub":"GitHub nicht erreichbar","(可稍后重试)":"(später erneut versuchen)","来源":"Quelle","升级失败":"Upgrade fehlgeschlagen","原密码不对":"Aktuelles Passwort ist falsch","修改失败":"Änderung fehlgeschlagen","上传失败":"Upload fehlgeschlagen","密码已更新, 其它设备需要重新登录":"Passwort aktualisiert, andere Geräte müssen sich neu anmelden","仅支持 css/jpg/png/webp":"Nur css/jpg/png/webp unterstützt","皮肤已上传并应用":"Skin hochgeladen und angewendet","文件超过 100MB 上限":"Datei überschreitet das Limit von 100 MB","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"USB-Stick im Fahrzeug verbunden (Fahrzeugzugriff vorübergehend getrennt, automatische Wiederherstellung nach 5 Minuten Inaktivität)","已恢复车机导出":"Fahrzeugexport wiederhergestellt","车机U盘未连接, 请先点「连接车机U盘」":"USB-Stick im Fahrzeug nicht verbunden, bitte zuerst 「USB-Stick im Fahrzeug verbinden」 antippen","归档里没有可用的片段":"Keine verwendbaren Clips im Archiv","请至少选择一路":"Bitte mindestens einen Kanal auswählen","已有合成在进行":"Zusammenführung läuft bereits","合成失败":"Zusammenführung fehlgeschlagen","已开始下载":"Download gestartet","此浏览器不支持本机合成, 请使用打包下载":"Dieser Browser unterstützt keine lokale Zusammenführung, bitte den Paket-Download verwenden","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"Zusammenfügen fertig","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"en":{"App ID — cli_ 开头":"App ID — starts with cli_","App Secret (已保存则留空表示不修改)":"App Secret (leave blank to keep current)","‹ 返回":"‹ Back","⏳ 待写入车机U盘:":"⏳ Pending write to car USB:","▶ 轻触开始同步播放":"▶ Tap to start synced playback","⚙ 个性化":"⚙ Personalize","✅ 已写入:":"✅ Written:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"Upload a CSS skin file or background image (jpg/png/webp). Reference images in CSS as /skin-media/filename.","下载":"Download","下载清晰度":"Download quality","主题":"Theme","也推手动保存":"Also push manual saves","仅支持 wav/mp3":"Only wav/mp3 supported","仅限本人访问":"Owner access only","今天":"Today","位置":"Location","使用中":"Active","侦测到物体移动":"Object movement detected","侦测到碰撞/震动":"Impact/vibration detected","保存失败":"Save failed","保存设置":"Save settings","修改密码":"Change password","全选":"Select all","全部":"All","全部日期":"All dates","再输一次新密码":"Repeat new password","删除":"Delete","刹车":"Brake","刹车占比":"Brake %","前视":"Front","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"The upgrade fetches the latest build from GitHub and multiple mirrors in parallel, overwrites local files and restarts the service automatically (about 5 s); configs, recordings and login info are unaffected.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"Single semi-transparent line: time/location/speed/gear/throttle/brake, barely covers the view","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"Single-select downloads the original file directly (with watermark it composites locally); multi-select merges one multi-view video. Downloads/zips export at the selected quality (first time may wait for transcoding).","原因":"Reason","原画":"Original","发送测试":"Send test","叠加行车信息水印":"Overlay driving data watermark","右B柱":"Right B-pillar","右前翼":"Right front fender","右翼子板":"Right fender","合成视频":"Merge video","同步状态":"Sync status","后视":"Rear","启用推送":"Enable push","哨兵告警":"Sentry alerts","哨兵模式触发":"Sentry mode triggered","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"When Sentry triggers, pushes time/location, trigger reason and the trigger frame to Feishu. Defaults to private messages via your own app (only you can see them); you can also switch to a group bot webhook (visible to the whole group). Video push works for private messages only.","哨兵警觉":"Sentry aware","回滚到上一版本":"Roll back to previous version","天":"days","实时同步中":"Live syncing","密码":"Password","尝试次数过多，请 5 分钟后再试":"Too many attempts, try again in 5 minutes","展开详细数据 ⌄":"Show details ⌄","左B柱":"Left B-pillar","左前翼":"Left front fender","左翼子板":"Left fender","已保存":"Saved","已删除":"Deleted","已加入队列, 归档同步时写入车机U盘":"Queued, will be written to car USB during archive sync","已同步":"Synced","已是最新":"Up to date","延迟推送":"Push delay","当前密码":"Current password","当前版本":"Current version","恢复车机导出":"Restore car access","手动保存":"Manual save","打包zip":"Zip download","按喇叭保存":"Horn save","换个日期或点「全部」查看本分类所有事件":"Try another date or tap \"All\" to see all events in this category","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"Recipient: your open_id (starts with ou_) or Feishu login email","推送内容：封面卡片（触发那帧图 + 信息，最快）":"Push content: cover card (trigger frame + info, fastest)","推送内容：触发摄像头视频（可回放）":"Push content: trigger camera video (playable)","摄像头":"Camera","收起详细数据 ⌃":"Hide details ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"After the change, this device logs back in with the new password automatically; login sessions on phones/other devices are invalidated immediately.","敏感信息已明文显示，注意别外发":"Sensitive info shown in plain text, be careful when sharing","敏感信息已隐藏（截图安全）":"Sensitive info hidden (screenshot-safe)","新密码 (至少 6 位)":"New password (at least 6 characters)","方向盘角度":"Steering angle","日期":"Date","时间":"Time","昨天":"Yesterday","暂无归档视频":"No archived videos yet","暂无皮肤, 使用默认外观":"No skins, using the default look","最新版本":"Latest version","有新版本":"New version available","未标注原因":"No reason tagged","未检查":"Not checked","条":"clips","查看链接前缀 (可选): http://你的域名:端口":"View link prefix (optional): http://your-domain:port","检查中…":"Checking…","检查更新":"Check for updates","正在上传…":"Uploading…","正在下载并校验新版本…":"Downloading and verifying new version…","正在合成多画面视频":"Compositing multi-view video","活动ID":"Event ID","浅色":"Light","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"Browse and manage the car USB drive (the mirror written by Tesla). Connecting briefly interrupts car read access for a few seconds; it auto-restores after 5 minutes of inactivity.","深色":"Dark","清空":"Clear","点击行车记录仪图标":"Dashcam icon tapped","片段":"Clips","用户名":"Username","用户名或密码不对":"Wrong username or password","画面":"Views","界面语言，保存在本机浏览器":"UI language, saved in this browser","登录":"Log in","登录状态保留":"Login remembered for","盒子归档存储":"Box archive storage","确定要退出登录吗？":"Are you sure you want to log out?","私聊推送 — 只有我本人能看到":"Direct message — only I can see it","秒 (等片段写完再推)":"seconds (wait for clips to finish writing)","秒（从触发前 5 秒开始截）":"seconds (starts 5 s before trigger)","立即升级":"Upgrade now","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"Group bot webhook (group push only): https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"Group bot — everyone in the group can see it","自动":"Auto","行车记录":"Dashcam","行车记录仪菜单保存":"Dashcam menu save","视频时长":"Clip length","视频清晰度：360p（约 1.8MB / 30 秒）":"Video quality: 360p (~1.8MB / 30 s)","视频清晰度：480p（约 3MB / 30 秒）":"Video quality: 480p (~3MB / 30 s)","视频清晰度：720p（约 5MB / 30 秒）":"Video quality: 720p (~5MB / 30 s)","触发原因":"Trigger reason","设为锁车音":"Set as lock chime","设备正在拼接各路画面, 请勿关闭页面":"Stitching camera views, do not close this page","设置":"Settings","该筛选下暂无事件":"No events for this filter","跳转":"Go","车机U盘未连接":"Car USB not connected","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"After the car records, archive sync pulls in videos automatically (mirror needs 10 min idle)","车机要求单个音频 <1MB, 请裁剪后重试":"Car requires each audio file <1MB, trim it and retry","车载U盘存储":"USB drive storage","车门拉动":"Door pull","车门被拉动":"Door pulled","载入中…":"Loading…","连接车机U盘":"Connect car USB","退出登录":"Log out","选择日期…":"Pick a date…","选择要导出的画面":"Select views to export","选择语言":"Select language","遭遇袭击":"Impact","遭遇袭击 (紧急报警)":"Impact (panic alarm)","锁车音":"Lock chime","锁车音必须是 WAV (mp3 只能当喇叭音效)":"Lock chime must be WAV (mp3 only works as a Boombox sound)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"Lock chime requires WAV 44.1kHz 16-bit <1MB 1–5 s; LockChime.wav goes to the root of the car USB drive, other mp3/wav files are written to the Boombox/ folder as Boombox sounds. Files are written to the car USB during archive sync.","门把手被拉动":"Door handle pulled","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ Upload file to current folder (any type, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ Upload skin file (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ Upload audio (wav/mp3, <1MB)","🌐 语言":"🌐 Language","🎨 网页背景":"🎨 Skin","👁 显示":"👁 Show","📂 文件":"📂 Files","🔄 更新":"🔄 Update","🔊 锁车语音":"🔊 Lock chime","🔑 账号":"🔑 Account","🔔 推送":"🔔 Push","🗄 归档区":"🗄 Archive area","🙈 隐藏":"🙈 Hide","🚗 特斯拉哨兵查看器":"🚗 Tesla Sentry Viewer","🚗 车机U盘":"🚗 Car USB","归档":"Archive","待确认":"Pending","同步服务未运行":"Sync service not running","路画面":"Channel view","最近一次":"Last run","未启用":"Not enabled","已启用私聊推送, 等下一个哨兵事件":"Direct messages enabled, waiting for the next Sentry event","已启用, 但接收人/应用凭据未配全":"Enabled, but recipient/app credentials are incomplete","读取设置失败":"Failed to load settings","私聊需要 App ID / Secret + 接收人":"Direct messages need App ID / Secret + recipient","请先填群机器人 webhook":"Fill in the group bot webhook first","保存中…":"Saving…","发送失败":"Send failed","正在发送测试卡片…":"Sending test card…","当前未开启登录保护, 设置密码后即开启":"Login protection is off, set a password to enable it","新密码至少 6 位":"New password must be at least 6 characters","两次输入的新密码不一致":"New passwords do not match","提交中…":"Submitting…","空目录":"Empty folder","正在连接 GitHub…":"Connecting to GitHub…","检查失败":"Check failed","连不上 GitHub":"Cannot reach GitHub","(可稍后重试)":"(you can retry later)","来源":"Source","升级失败":"Upgrade failed","原密码不对":"Current password is incorrect","修改失败":"Update failed","上传失败":"Upload failed","密码已更新, 其它设备需要重新登录":"Password updated, other devices need to sign in again","仅支持 css/jpg/png/webp":"Only css/jpg/png/webp supported","皮肤已上传并应用":"Skin uploaded and applied","文件超过 100MB 上限":"File exceeds the 100 MB limit","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"Car USB drive connected (car access temporarily released, auto-restores after 5 minutes idle)","已恢复车机导出":"Car export restored","车机U盘未连接, 请先点「连接车机U盘」":"Car USB drive not connected, tap 「Connect car USB drive」 first","归档里没有可用的片段":"No usable clips in the archive","请至少选择一路":"Select at least one channel","已有合成在进行":"A merge is already in progress","合成失败":"Merge failed","已开始下载":"Download started","此浏览器不支持本机合成, 请使用打包下载":"This browser does not support local merging, use the packaged download","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"Merge complete","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"es":{"App ID — cli_ 开头":"App ID — empieza por cli_","App Secret (已保存则留空表示不修改)":"App Secret (déjalo vacío para no cambiarlo)","‹ 返回":"‹ Volver","⏳ 待写入车机U盘:":"⏳ Pendiente de escribir en el USB del coche:","▶ 轻触开始同步播放":"▶ Toca para iniciar la reproducción sincronizada","⚙ 个性化":"⚙ Personalización","✅ 已写入:":"✅ Escritos:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"Sube un archivo de skin CSS o una imagen de fondo (jpg/png/webp). Para las imágenes en el CSS usa /skin-media/nombrearchivo.","下载":"Descargar","下载清晰度":"Calidad de descarga","主题":"Tema","也推手动保存":"También los guardados manuales","仅支持 wav/mp3":"Solo wav/mp3","仅限本人访问":"Acceso solo para el propietario","今天":"Hoy","位置":"Ubicación","使用中":"En uso","侦测到物体移动":"Movimiento detectado","侦测到碰撞/震动":"Colisión/vibración detectada","保存失败":"Error al guardar","保存设置":"Guardar ajustes","修改密码":"Cambiar contraseña","全选":"Seleccionar todo","全部":"Todos","全部日期":"Todas las fechas","再输一次新密码":"Repite la nueva contraseña","删除":"Eliminar","刹车":"Freno","刹车占比":"Porcentaje de freno","前视":"Delantera","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"La actualización descarga la última versión en paralelo desde GitHub y varios espejos, sobrescribe los archivos locales y reinicia el servicio (unos 5 s); la configuración, las grabaciones y el acceso no se ven afectados.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"Una línea semitransparente: hora/ubicación/velocidad/marcha/acelerador/freno, apenas tapa la imagen","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"Con una sola selección descargas el archivo original (con marca de agua se compone en local); con varias se crea un único vídeo multivista. La descarga y el zip se exportan en la calidad elegida (la primera vez hay que esperar la transcodificación).","原因":"Motivo","原画":"Original","发送测试":"Enviar prueba","叠加行车信息水印":"Añadir marca de agua con datos de conducción","右B柱":"Pilar B der.","右前翼":"Aleta delantera der.","右翼子板":"Aleta der.","合成视频":"Unir vídeo","同步状态":"Estado de sync","后视":"Trasera","启用推送":"Activar notificaciones","哨兵告警":"Alertas Centinela","哨兵模式触发":"Activación del Centinela","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"Cuando salta el Centinela envía a Feishu la hora/ubicación/causa y el fotograma del aviso. Por defecto usa la app privada (solo lo ves tú); también puedes cambiar al webhook de un bot de grupo (lo ve todo el grupo). El push de vídeo solo funciona en privado.","哨兵警觉":"Alerta centinela","回滚到上一版本":"Volver a la versión anterior","天":"días","实时同步中":"Sincronizando en vivo","密码":"Contraseña","尝试次数过多，请 5 分钟后再试":"Demasiados intentos, inténtalo en 5 minutos","展开详细数据 ⌄":"Mostrar datos detallados ⌄","左B柱":"Pilar B izq.","左前翼":"Aleta delantera izq.","左翼子板":"Aleta izq.","已保存":"Guardado","已删除":"Eliminado","已加入队列, 归档同步时写入车机U盘":"En cola, se escribirá en el USB del coche al sincronizar el archivo","已同步":"Sincronizado","已是最新":"Ya está actualizado","延迟推送":"Push retrasado","当前密码":"Contraseña actual","当前版本":"Versión actual","恢复车机导出":"Restaurar acceso del coche","手动保存":"Guardados manuales","打包zip":"Crear zip","按喇叭保存":"Guardado con claxon","换个日期或点「全部」查看本分类所有事件":"Cambia la fecha o toca «Todos» para ver todos los eventos de la categoría","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"Destinatario: tu open_id (empieza por ou_) o el correo de acceso a Feishu","推送内容：封面卡片（触发那帧图 + 信息，最快）":"Contenido: tarjeta de portada (fotograma del aviso + info, lo más rápido)","推送内容：触发摄像头视频（可回放）":"Contenido: vídeo de la cámara que lo activó (reproducible)","摄像头":"Cámara","收起详细数据 ⌃":"Ocultar datos detallados ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"Tras el cambio, este dispositivo inicia sesión con la nueva contraseña; las sesiones del móvil y otros dispositivos se cierran al instante.","敏感信息已明文显示，注意别外发":"Datos sensibles a la vista, no los compartas","敏感信息已隐藏（截图安全）":"Datos sensibles ocultos (seguro para capturas)","新密码 (至少 6 位)":"Nueva contraseña (mínimo 6 caracteres)","方向盘角度":"Ángulo de dirección","日期":"Fecha","时间":"Hora","昨天":"Ayer","暂无归档视频":"No hay vídeos archivados","暂无皮肤, 使用默认外观":"Sin skins, aspecto predeterminado","最新版本":"Última versión","有新版本":"Nueva versión disponible","未标注原因":"Motivo no indicado","未检查":"Sin comprobar","条":"clips","查看链接前缀 (可选): http://你的域名:端口":"Prefijo del enlace de vista (opcional): http://tu-dominio:puerto","检查中…":"Comprobando…","检查更新":"Buscar actualizaciones","正在上传…":"Subiendo…","正在下载并校验新版本…":"Descargando y verificando la nueva versión…","正在合成多画面视频":"Creando vídeo multivista","活动ID":"ID del evento","浅色":"Claro","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"Explora y gestiona el USB del coche (la copia que escribe Tesla). Al conectar, el coche pierde el acceso unos segundos; se restaura tras 5 minutos sin actividad.","深色":"Oscuro","清空":"Deseleccionar todo","点击行车记录仪图标":"Toque en el icono Dashcam","片段":"Clips","用户名":"Usuario","用户名或密码不对":"Usuario o contraseña incorrectos","画面":"Vistas","界面语言，保存在本机浏览器":"Idioma de la interfaz, guardado en este navegador","登录":"Iniciar sesión","登录状态保留":"Mantener la sesión","盒子归档存储":"Espacio de archivo del box","确定要退出登录吗？":"¿Seguro que quieres cerrar sesión?","私聊推送 — 只有我本人能看到":"Push privado — solo lo ves tú","秒 (等片段写完再推)":"segundos (espera a que se escriban los clips)","秒（从触发前 5 秒开始截）":"segundos (desde 5 s antes del aviso)","立即升级":"Actualizar ahora","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"webhook del bot de grupo (solo para push de grupo): https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"Bot de grupo — lo ve todo el grupo","自动":"Auto","行车记录":"Dashcam","行车记录仪菜单保存":"Guardado desde el menú Dashcam","视频时长":"Duración del vídeo","视频清晰度：360p（约 1.8MB / 30 秒）":"Calidad de vídeo: 360p (unos 1.8MB / 30 s)","视频清晰度：480p（约 3MB / 30 秒）":"Calidad de vídeo: 480p (unos 3MB / 30 s)","视频清晰度：720p（约 5MB / 30 秒）":"Calidad de vídeo: 720p (unos 5MB / 30 s)","触发原因":"Causa de activación","设为锁车音":"Usar como sonido al cerrar","设备正在拼接各路画面, 请勿关闭页面":"El dispositivo está uniendo las vistas, no cierres la página","设置":"Ajustes","该筛选下暂无事件":"No hay eventos con este filtro","跳转":"Ir","车机U盘未连接":"USB del coche no conectado","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"Tras grabar el coche, la sincronización del archivo recoge los vídeos (el espejo debe estar inactivo 10 minutos)","车机要求单个音频 <1MB, 请裁剪后重试":"El coche exige audio <1MB, recorta el archivo e inténtalo de nuevo","车载U盘存储":"Espacio USB del coche","车门拉动":"Puerta forzada","车门被拉动":"Puerta manipulada","载入中…":"Cargando…","连接车机U盘":"Conectar USB del coche","退出登录":"Cerrar sesión","选择日期…":"Elegir fecha…","选择要导出的画面":"Elige las vistas que quieres exportar","选择语言":"Elegir idioma","遭遇袭击":"Agresión","遭遇袭击 (紧急报警)":"Agresión (alarma de emergencia)","锁车音":"Sonido al cerrar","锁车音必须是 WAV (mp3 只能当喇叭音效)":"El sonido al cerrar debe ser WAV (mp3 solo como efecto de claxon)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"El sonido al cerrar debe ser WAV 44.1kHz 16-bit <1MB 1~5 s; LockChime.wav se escribe en la raíz del USB del coche y los demás mp3/wav como efectos de Boombox en la carpeta Boombox/. Los archivos se escriben en el USB del coche al sincronizar el archivo.","门把手被拉动":"Manilla accionada","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ Subir archivo a la carpeta actual (cualquier tipo, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ Subir archivo de skin (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ Subir audio (wav/mp3, <1MB)","🌐 语言":"🌐 Idioma","🎨 网页背景":"🎨 Apariencia","👁 显示":"👁 Mostrar","📂 文件":"📂 Archivos","🔄 更新":"🔄 Actualización","🔊 锁车语音":"🔊 Sonido al cerrar","🔑 账号":"🔑 Cuenta","🔔 推送":"🔔 Notificaciones","🗄 归档区":"🗄 Archivo","🙈 隐藏":"🙈 Ocultar","🚗 特斯拉哨兵查看器":"🚗 Visor Centinela Tesla","🚗 车机U盘":"🚗 USB del coche","归档":"Archivo","待确认":"Pendiente de confirmar","同步服务未运行":"Servicio de sincronización no activo","路画面":"Vista de canal","最近一次":"Última vez","未启用":"No activado","已启用私聊推送, 等下一个哨兵事件":"Avisos privados activados, esperando el próximo evento Centinela","已启用, 但接收人/应用凭据未配全":"Activado, pero faltan destinatarios o credenciales de la app","读取设置失败":"No se pudo leer la configuración","私聊需要 App ID / Secret + 接收人":"Los avisos privados requieren App ID / Secret + destinatario","请先填群机器人 webhook":"Primero rellena el webhook del bot de grupo","保存中…":"Guardando…","发送失败":"Error al enviar","正在发送测试卡片…":"Enviando tarjeta de prueba…","当前未开启登录保护, 设置密码后即开启":"Protección de acceso desactivada: se activa al definir una contraseña","新密码至少 6 位":"La nueva contraseña debe tener al menos 6 caracteres","两次输入的新密码不一致":"Las contraseñas nuevas no coinciden","提交中…":"Enviando…","空目录":"Carpeta vacía","正在连接 GitHub…":"Conectando con GitHub…","检查失败":"Error en la comprobación","连不上 GitHub":"No se puede conectar con GitHub","(可稍后重试)":"(puedes reintentarlo más tarde)","来源":"Origen","升级失败":"Error en la actualización","原密码不对":"La contraseña actual es incorrecta","修改失败":"Error al modificar","上传失败":"Error al subir","密码已更新, 其它设备需要重新登录":"Contraseña actualizada, los demás dispositivos deben volver a iniciar sesión","仅支持 css/jpg/png/webp":"Solo css/jpg/png/webp","皮肤已上传并应用":"Tema subido y aplicado","文件超过 100MB 上限":"El archivo supera el límite de 100MB","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"Memoria USB del coche conectada (lectura del coche interrumpida, se restaura automáticamente tras 5 minutos sin actividad)","已恢复车机导出":"Exportación del coche restaurada","车机U盘未连接, 请先点「连接车机U盘」":"Memoria USB del coche no conectada, pulsa antes «Conectar memoria USB del coche»","归档里没有可用的片段":"No hay clips disponibles en el archivo","请至少选择一路":"Selecciona al menos un canal","已有合成在进行":"Ya hay una composición en curso","合成失败":"Error en la composición","已开始下载":"Descarga iniciada","此浏览器不支持本机合成, 请使用打包下载":"Este navegador no admite la composición local, usa la descarga empaquetada","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"Unión completada","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"fr":{"App ID — cli_ 开头":"App ID — commence par cli_","App Secret (已保存则留空表示不修改)":"App Secret (laisser vide pour ne pas modifier)","‹ 返回":"‹ Retour","⏳ 待写入车机U盘:":"⏳ En attente d'écriture sur l'USB :","▶ 轻触开始同步播放":"▶ Touchez pour lancer la lecture synchro","⚙ 个性化":"⚙ Personnalisation","✅ 已写入:":"✅ Écrit :","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"Chargez un fichier de skin CSS ou une image de fond (jpg/png/webp). Dans le CSS, référencez les images via /skin-media/nomfichier.","下载":"Télécharger","下载清晰度":"Qualité de téléchargement","主题":"Thème","也推手动保存":"Notifier aussi les enregistrements manuels","仅支持 wav/mp3":"Formats acceptés : wav/mp3","仅限本人访问":"Accès réservé au propriétaire","今天":"Aujourd'hui","位置":"Position","使用中":"Actif","侦测到物体移动":"Mouvement détecté","侦测到碰撞/震动":"Impact/vibration détecté","保存失败":"Échec de l'enregistrement","保存设置":"Enregistrer les réglages","修改密码":"Modifier le mot de passe","全选":"Tout sélectionner","全部":"Tout","全部日期":"Toutes les dates","再输一次新密码":"Ressaisir le nouveau mot de passe","删除":"Supprimer","刹车":"Frein","刹车占比":"% de freinage","前视":"Avant","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"La mise à jour récupère la dernière version depuis GitHub et plusieurs miroirs en parallèle, remplace les fichiers locaux et redémarre le service automatiquement (environ 5 s) ; configuration, enregistrements et connexion restent inchangés.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"Ligne semi-transparente : heure/position/vitesse/rapport/pédale/frein, gêne à peine la vue","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"Sélection simple : téléchargement du fichier original (avec filigrane, composition locale) ; sélection multiple : fusion en une vidéo multi-vues. Téléchargement/zip exportés à la qualité choisie (première fois : attente de transcodage).","原因":"Motif","原画":"Originale","发送测试":"Envoyer un test","叠加行车信息水印":"Incruster les infos de conduite","右B柱":"Montant B droit","右前翼":"Aile avant droite","右翼子板":"Aile droite","合成视频":"Fusionner en vidéo","同步状态":"État de synchronisation","后视":"Arrière","启用推送":"Activer les notifications","哨兵告警":"Alertes Sentry","哨兵模式触发":"Mode Sentry déclenché","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"Au déclenchement Sentry, envoie heure/position, motif de déclenchement et image du déclenchement vers Feishu. Par défaut, messages privés via votre propre app (visibles par vous seul) ; sinon webhook de bot de groupe (visible par tout le groupe). L'envoi vidéo fonctionne uniquement en privé.","哨兵警觉":"Vigilance Sentry","回滚到上一版本":"Revenir à la version précédente","天":"jours","实时同步中":"Synchro en cours","密码":"Mot de passe","尝试次数过多，请 5 分钟后再试":"Trop de tentatives, réessayez dans 5 minutes","展开详细数据 ⌄":"Déplier les détails ⌄","左B柱":"Montant B gauche","左前翼":"Aile avant gauche","左翼子板":"Aile gauche","已保存":"Enregistré","已删除":"Supprimé","已加入队列, 归档同步时写入车机U盘":"En file d'attente, écrit sur l'USB voiture lors de la synchro d'archive","已同步":"Synchronisé","已是最新":"À jour","延迟推送":"Délai d'envoi","当前密码":"Mot de passe actuel","当前版本":"Version actuelle","恢复车机导出":"Restaurer l'accès voiture","手动保存":"Enregistrement manuel","打包zip":"Archive zip","按喇叭保存":"Enregistrement au klaxon","换个日期或点「全部」查看本分类所有事件":"Choisissez une autre date ou touchez « Tout » pour voir tous les événements de cette catégorie","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"Destinataire : votre open_id (commence par ou_) ou e-mail de connexion Feishu","推送内容：封面卡片（触发那帧图 + 信息，最快）":"Contenu : carte de couverture (image du déclenchement + infos, le plus rapide)","推送内容：触发摄像头视频（可回放）":"Contenu : vidéo de la caméra de déclenchement (lecture possible)","摄像头":"Caméra","收起详细数据 ⌃":"Réduire les détails ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"Après la modification, cet appareil se reconnecte automatiquement avec le nouveau mot de passe ; les sessions sur mobile/autres appareils sont immédiatement invalidées.","敏感信息已明文显示，注意别外发":"Infos sensibles en clair, attention au partage","敏感信息已隐藏（截图安全）":"Infos sensibles masquées (sûr pour captures)","新密码 (至少 6 位)":"Nouveau mot de passe (6 caractères minimum)","方向盘角度":"Angle du volant","日期":"Date","时间":"Heure","昨天":"Hier","暂无归档视频":"Aucune vidéo archivée","暂无皮肤, 使用默认外观":"Aucun skin, apparence par défaut","最新版本":"Dernière version","有新版本":"Nouvelle version disponible","未标注原因":"Motif non renseigné","未检查":"Non vérifié","条":"clips","查看链接前缀 (可选): http://你的域名:端口":"Préfixe du lien de visualisation (optionnel) : http://votre-domaine:port","检查中…":"Vérification…","检查更新":"Rechercher des mises à jour","正在上传…":"Envoi en cours…","正在下载并校验新版本…":"Téléchargement et vérification de la nouvelle version…","正在合成多画面视频":"Fusion de la vidéo multi-vues","活动ID":"ID d'événement","浅色":"Clair","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"Parcourez et gérez l'USB voiture (le miroir écrit par Tesla). La connexion interrompt brièvement la lecture par la voiture quelques secondes ; restauration automatique après 5 minutes d'inactivité.","深色":"Sombre","清空":"Vider","点击行车记录仪图标":"Icône dashcam touchée","片段":"Clips","用户名":"Nom d'utilisateur","用户名或密码不对":"Nom d'utilisateur ou mot de passe incorrect","画面":"Vues","界面语言，保存在本机浏览器":"Langue de l'interface, mémorisée dans ce navigateur","登录":"Se connecter","登录状态保留":"Connexion conservée","盒子归档存储":"Stockage archive Box","确定要退出登录吗？":"Voulez-vous vraiment vous déconnecter ?","私聊推送 — 只有我本人能看到":"Message privé — visible uniquement par moi","秒 (等片段写完再推)":"secondes (attendre la fin d'écriture des clips)","秒（从触发前 5 秒开始截）":"secondes (commence 5 s avant le déclenchement)","立即升级":"Mettre à jour maintenant","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"Webhook du bot de groupe (groupes uniquement) : https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"Bot de groupe — visible par tous dans le groupe","自动":"Auto","行车记录":"Dashcam","行车记录仪菜单保存":"Enregistrement via menu dashcam","视频时长":"Durée de la vidéo","视频清晰度：360p（约 1.8MB / 30 秒）":"Qualité vidéo : 360p (~1,8MB / 30 s)","视频清晰度：480p（约 3MB / 30 秒）":"Qualité vidéo : 480p (~3MB / 30 s)","视频清晰度：720p（约 5MB / 30 秒）":"Qualité vidéo : 720p (~5MB / 30 s)","触发原因":"Motif de déclenchement","设为锁车音":"Définir comme son de verrouillage","设备正在拼接各路画面, 请勿关闭页面":"Assemblage des vues en cours, ne fermez pas la page","设置":"Paramètres","该筛选下暂无事件":"Aucun événement pour ce filtre","跳转":"Aller","车机U盘未连接":"USB voiture non connecté","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"Après enregistrement par la voiture, la synchro d'archive importe les vidéos automatiquement (le miroir doit être inactif 10 min)","车机要求单个音频 <1MB, 请裁剪后重试":"La voiture exige un audio <1MB, raccourcissez et réessayez","车载U盘存储":"Stockage USB embarqué","车门拉动":"Tirage de portière","车门被拉动":"Portière manipulée","载入中…":"Chargement…","连接车机U盘":"Connecter l'USB voiture","退出登录":"Se déconnecter","选择日期…":"Choisir une date…","选择要导出的画面":"Choisir les vues à exporter","选择语言":"Choisir la langue","遭遇袭击":"Impact","遭遇袭击 (紧急报警)":"Impact (alarme d'urgence)","锁车音":"Son de verrouillage","锁车音必须是 WAV (mp3 只能当喇叭音效)":"Le son de verrouillage doit être WAV (mp3 réservé aux sons Boombox)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"Le son de verrouillage exige WAV 44.1kHz 16-bit <1MB 1 à 5 s ; LockChime.wav va à la racine de l'USB voiture, les autres mp3/wav sont écrits dans le dossier Boombox/ comme sons Boombox. Les fichiers sont écrits sur l'USB voiture lors de la synchro d'archive.","门把手被拉动":"Poignée tirée","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ Charger un fichier ici (tout type, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ Charger un fichier de skin (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ Charger un audio (wav/mp3, <1MB)","🌐 语言":"🌐 Langue","🎨 网页背景":"🎨 Habillage","👁 显示":"👁 Afficher","📂 文件":"📂 Fichiers","🔄 更新":"🔄 Mise à jour","🔊 锁车语音":"🔊 Son de verrouillage","🔑 账号":"🔑 Compte","🔔 推送":"🔔 Notifications","🗄 归档区":"🗄 Zone d'archive","🙈 隐藏":"🙈 Masquer","🚗 特斯拉哨兵查看器":"🚗 Tesla Sentry Viewer","🚗 车机U盘":"🚗 USB voiture","归档":"Archives","待确认":"En attente","同步服务未运行":"Service de synchronisation inactif","路画面":"Vue par canal","最近一次":"Dernière exécution","未启用":"Non activé","已启用私聊推送, 等下一个哨兵事件":"Messages privés activés, en attente du prochain événement Sentry","已启用, 但接收人/应用凭据未配全":"Activé, mais destinataire/identifiants d'application incomplets","读取设置失败":"Échec du chargement des paramètres","私聊需要 App ID / Secret + 接收人":"Les messages privés nécessitent App ID / Secret + destinataire","请先填群机器人 webhook":"Renseignez d'abord le webhook du bot de groupe","保存中…":"Enregistrement…","发送失败":"Échec de l'envoi","正在发送测试卡片…":"Envoi de la carte de test…","当前未开启登录保护, 设置密码后即开启":"La protection de connexion est désactivée, définissez un mot de passe pour l'activer","新密码至少 6 位":"Nouveau mot de passe : 6 caractères minimum","两次输入的新密码不一致":"Les nouveaux mots de passe ne correspondent pas","提交中…":"Envoi…","空目录":"Dossier vide","正在连接 GitHub…":"Connexion à GitHub…","检查失败":"Échec de la vérification","连不上 GitHub":"GitHub inaccessible","(可稍后重试)":"(réessayez plus tard)","来源":"Source","升级失败":"Échec de la mise à niveau","原密码不对":"Mot de passe actuel incorrect","修改失败":"Échec de la modification","上传失败":"Échec du téléversement","密码已更新, 其它设备需要重新登录":"Mot de passe mis à jour, les autres appareils doivent se reconnecter","仅支持 css/jpg/png/webp":"Seuls css/jpg/png/webp sont pris en charge","皮肤已上传并应用":"Skin téléversé et appliqué","文件超过 100MB 上限":"Le fichier dépasse la limite de 100 Mo","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"Clé USB du véhicule connectée (accès véhicule temporairement libéré, rétablissement automatique après 5 minutes d'inactivité)","已恢复车机导出":"Export véhicule rétabli","车机U盘未连接, 请先点「连接车机U盘」":"Clé USB du véhicule non connectée, touchez d'abord 「Connecter la clé USB du véhicule」","归档里没有可用的片段":"Aucun clip utilisable dans les archives","请至少选择一路":"Sélectionnez au moins un canal","已有合成在进行":"Une fusion est déjà en cours","合成失败":"Échec de la fusion","已开始下载":"Téléchargement démarré","此浏览器不支持本机合成, 请使用打包下载":"Ce navigateur ne prend pas en charge la fusion locale, utilisez le téléchargement groupé","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"Fusion terminée","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"it":{"App ID — cli_ 开头":"App ID — inizia con cli_","App Secret (已保存则留空表示不修改)":"App Secret (lascia vuoto per non modificarlo)","‹ 返回":"‹ Indietro","⏳ 待写入车机U盘:":"⏳ Da scrivere sull'USB dell'auto:","▶ 轻触开始同步播放":"▶ Tocca per avviare la riproduzione sincronizzata","⚙ 个性化":"⚙ Personalizzazione","✅ 已写入:":"✅ Scritti:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"Carica un file skin CSS o un'immagine di sfondo (jpg/png/webp). Per le immagini nei CSS usa /skin-media/nomefile.","下载":"Scarica","下载清晰度":"Qualità download","主题":"Tema","也推手动保存":"Anche i salvataggi manuali","仅支持 wav/mp3":"Solo wav/mp3","仅限本人访问":"Accesso riservato al proprietario","今天":"Oggi","位置":"Posizione","使用中":"In uso","侦测到物体移动":"Movimento rilevato","侦测到碰撞/震动":"Collisione/urto rilevato","保存失败":"Salvataggio non riuscito","保存设置":"Salva impostazioni","修改密码":"Cambia password","全选":"Seleziona tutto","全部":"Tutti","全部日期":"Tutte le date","再输一次新密码":"Ripeti la nuova password","删除":"Elimina","刹车":"Freno","刹车占比":"Percentuale frenata","前视":"Anteriore","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"L'aggiornamento scarica l'ultima versione in parallelo da GitHub e da più mirror, sovrascrive i file locali e riavvia il servizio (circa 5 s); configurazione, registrazioni e accesso restano invariati.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"Una riga semitrasparente: ora/posizione/velocità/marcia/acceleratore/freno, non copre quasi nulla","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"Con una sola selezione scarichi il file originale (con watermark viene ricomposto in locale); con più selezioni crei un unico video multi-vista. Download e zip esportano alla qualità scelta (la prima volta serve la transcodifica).","原因":"Motivo","原画":"Originale","发送测试":"Invia test","叠加行车信息水印":"Aggiungi watermark con i dati di guida","右B柱":"B-pilastro dx","右前翼":"Parafango ant. dx","右翼子板":"Parafango dx","合成视频":"Unisci video","同步状态":"Stato sync","后视":"Posteriore","启用推送":"Attiva notifiche","哨兵告警":"Avvisi Sentinella","哨兵模式触发":"Attivazione Sentinella","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"Quando scatta la Sentinella invia a Feishu ora/posizione/causa e il frame del trigger. Per impostazione predefinita usa l'app privata (lo vedi solo tu); puoi passare al webhook di un bot di gruppo (lo vede tutto il gruppo). Il push dei video è solo in privato.","哨兵警觉":"Allerta sentinella","回滚到上一版本":"Torna alla versione precedente","天":"giorni","实时同步中":"Sync in tempo reale","密码":"Password","尝试次数过多，请 5 分钟后再试":"Troppi tentativi, riprova tra 5 minuti","展开详细数据 ⌄":"Mostra dati dettagliati ⌄","左B柱":"B-pilastro sx","左前翼":"Parafango ant. sx","左翼子板":"Parafango sx","已保存":"Salvato","已删除":"Eliminato","已加入队列, 归档同步时写入车机U盘":"Aggiunto in coda, verrà scritto sull'USB dell'auto durante la sincronizzazione archivio","已同步":"Sincronizzato","已是最新":"Già aggiornato","延迟推送":"Push ritardato","当前密码":"Password attuale","当前版本":"Versione attuale","恢复车机导出":"Ripristina accesso auto","手动保存":"Salvataggi manuali","打包zip":"Crea zip","按喇叭保存":"Salvataggio con clacson","换个日期或点「全部」查看本分类所有事件":"Cambia data o tocca «Tutti» per vedere tutti gli eventi della categoria","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"Destinatario: il tuo open_id (inizia con ou_) o l'email di accesso Feishu","推送内容：封面卡片（触发那帧图 + 信息，最快）":"Contenuto: scheda copertina (frame del trigger + info, il più veloce)","推送内容：触发摄像头视频（可回放）":"Contenuto: video della telecamera che ha attivato (riproducibile)","摄像头":"Telecamera","收起详细数据 ⌃":"Nascondi dati dettagliati ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"Dopo la modifica questo dispositivo accede automaticamente con la nuova password; le sessioni su telefono e altri dispositivi decadono subito.","敏感信息已明文显示，注意别外发":"Dati sensibili in chiaro, non condividerli","敏感信息已隐藏（截图安全）":"Dati sensibili nascosti (sicuro per gli screenshot)","新密码 (至少 6 位)":"Nuova password (almeno 6 caratteri)","方向盘角度":"Angolo sterzo","日期":"Data","时间":"Ora","昨天":"Ieri","暂无归档视频":"Nessun video archiviato","暂无皮肤, 使用默认外观":"Nessuna skin, aspetto predefinito","最新版本":"Ultima versione","有新版本":"Nuova versione disponibile","未标注原因":"Motivo non indicato","未检查":"Non verificato","条":"clip","查看链接前缀 (可选): http://你的域名:端口":"Prefisso link di visualizzazione (facoltativo): http://tuo-dominio:porta","检查中…":"Controllo…","检查更新":"Controlla aggiornamenti","正在上传…":"Caricamento in corso…","正在下载并校验新版本…":"Download e verifica della nuova versione…","正在合成多画面视频":"Creazione video multi-vista in corso","活动ID":"ID evento","浅色":"Chiaro","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"Sfoglia e gestisci l'USB dell'auto (la copia scritta da Tesla). Al collegamento l'auto perde l'accesso per qualche secondo; dopo 5 minuti di inattività viene ripristinato.","深色":"Scuro","清空":"Deseleziona tutto","点击行车记录仪图标":"Tocco sull'icona Dashcam","片段":"Clip","用户名":"Nome utente","用户名或密码不对":"Nome utente o password errati","画面":"Viste","界面语言，保存在本机浏览器":"Lingua dell'interfaccia, salvata in questo browser","登录":"Accedi","登录状态保留":"Mantieni l'accesso per","盒子归档存储":"Spazio archivio box","确定要退出登录吗？":"Vuoi davvero uscire?","私聊推送 — 只有我本人能看到":"Push in privato — visibile solo a te","秒 (等片段写完再推)":"secondi (attendi la scrittura delle clip)","秒（从触发前 5 秒开始截）":"secondi (a partire da 5 s prima del trigger)","立即升级":"Aggiorna ora","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"webhook bot di gruppo (solo per push di gruppo): https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"Bot di gruppo — visibile a tutto il gruppo","自动":"Auto","行车记录":"Dashcam","行车记录仪菜单保存":"Salvataggio dal menu Dashcam","视频时长":"Durata video","视频清晰度：360p（约 1.8MB / 30 秒）":"Qualità video: 360p (circa 1.8MB / 30 s)","视频清晰度：480p（约 3MB / 30 秒）":"Qualità video: 480p (circa 3MB / 30 s)","视频清晰度：720p（约 5MB / 30 秒）":"Qualità video: 720p (circa 5MB / 30 s)","触发原因":"Causa attivazione","设为锁车音":"Imposta come suono di chiusura","设备正在拼接各路画面, 请勿关闭页面":"Il dispositivo sta unendo le viste, non chiudere la pagina","设置":"Impostazioni","该筛选下暂无事件":"Nessun evento con questo filtro","跳转":"Vai","车机U盘未连接":"USB dell'auto non connesso","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"Dopo la registrazione dell'auto, la sincronizzazione archivio importa i video (il mirror deve restare inattivo 10 minuti)","车机要求单个音频 <1MB, 请裁剪后重试":"L'auto richiede file audio <1MB, taglia il file e riprova","车载U盘存储":"Spazio USB auto","车门拉动":"Portiera forzata","车门被拉动":"Portiera tirata","载入中…":"Caricamento…","连接车机U盘":"Collega USB auto","退出登录":"Esci","选择日期…":"Scegli data…","选择要导出的画面":"Scegli le viste da esportare","选择语言":"Scegli la lingua","遭遇袭击":"Aggressione","遭遇袭击 (紧急报警)":"Aggressione (allarme di emergenza)","锁车音":"Suono di chiusura","锁车音必须是 WAV (mp3 只能当喇叭音效)":"Il suono di chiusura deve essere WAV (mp3 solo come effetto clacson)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"Il suono di chiusura richiede WAV 44.1kHz 16-bit <1MB 1~5 s; LockChime.wav viene scritto nella radice dell'USB dell'auto, gli altri mp3/wav come effetti Boombox nella cartella Boombox/. I file vengono scritti sull'USB dell'auto durante la sincronizzazione archivio.","门把手被拉动":"Maniglia tirata","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ Carica file nella cartella corrente (qualsiasi tipo, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ Carica file skin (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ Carica audio (wav/mp3, <1MB)","🌐 语言":"🌐 Lingua","🎨 网页背景":"🎨 Tema grafico","👁 显示":"👁 Mostra","📂 文件":"📂 File","🔄 更新":"🔄 Aggiornamenti","🔊 锁车语音":"🔊 Suono di chiusura","🔑 账号":"🔑 Account","🔔 推送":"🔔 Notifiche","🗄 归档区":"🗄 Archivio","🙈 隐藏":"🙈 Nascondi","🚗 特斯拉哨兵查看器":"🚗 Visualizzatore Sentinella Tesla","🚗 车机U盘":"🚗 USB auto","归档":"Archivio","待确认":"Da confermare","同步服务未运行":"Servizio di sincronizzazione non attivo","路画面":"Vista canale","最近一次":"Ultima volta","未启用":"Non attivo","已启用私聊推送, 等下一个哨兵事件":"Notifiche private attive, in attesa del prossimo evento Sentinella","已启用, 但接收人/应用凭据未配全":"Attivo, ma destinatari/credenziali app incompleti","读取设置失败":"Lettura impostazioni non riuscita","私聊需要 App ID / Secret + 接收人":"I messaggi privati richiedono App ID / Secret + destinatario","请先填群机器人 webhook":"Compila prima il webhook del bot di gruppo","保存中…":"Salvataggio…","发送失败":"Invio non riuscito","正在发送测试卡片…":"Invio della card di test…","当前未开启登录保护, 设置密码后即开启":"Protezione accesso disattivata: si attiva impostando una password","新密码至少 6 位":"La nuova password deve avere almeno 6 caratteri","两次输入的新密码不一致":"Le nuove password non coincidono","提交中…":"Invio in corso…","空目录":"Cartella vuota","正在连接 GitHub…":"Connessione a GitHub…","检查失败":"Controllo non riuscito","连不上 GitHub":"Impossibile raggiungere GitHub","(可稍后重试)":"(puoi riprovare più tardi)","来源":"Origine","升级失败":"Aggiornamento non riuscito","原密码不对":"Password attuale errata","修改失败":"Modifica non riuscita","上传失败":"Caricamento non riuscito","密码已更新, 其它设备需要重新登录":"Password aggiornata, gli altri dispositivi devono riaccedere","仅支持 css/jpg/png/webp":"Solo css/jpg/png/webp","皮肤已上传并应用":"Tema caricato e applicato","文件超过 100MB 上限":"File oltre il limite di 100MB","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"Chiavetta USB dell'auto collegata (lettura dall'auto sospesa, ripristino automatico dopo 5 minuti di inattività)","已恢复车机导出":"Esportazione dall'auto ripristinata","车机U盘未连接, 请先点「连接车机U盘」":"Chiavetta USB dell'auto non collegata, tocca prima «Collega chiavetta USB auto»","归档里没有可用的片段":"Nessun clip disponibile nell'archivio","请至少选择一路":"Seleziona almeno un canale","已有合成在进行":"Composizione già in corso","合成失败":"Composizione non riuscita","已开始下载":"Download avviato","此浏览器不支持本机合成, 请使用打包下载":"Questo browser non supporta la composizione locale, usa il download in pacchetto","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"Unione completata","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"ja":{"App ID — cli_ 开头":"App ID — cli_ で始まる","App Secret (已保存则留空表示不修改)":"App Secret（保存済みなら空欄で変更なし）","‹ 返回":"‹ 戻る","⏳ 待写入车机U盘:":"⏳ 車載USBへの書き込み待ち:","▶ 轻触开始同步播放":"▶ タップして同期再生を開始","⚙ 个性化":"⚙ カスタマイズ","✅ 已写入:":"✅ 書き込み済み:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"CSSスキンまたは背景画像（jpg/png/webp）をアップロード。CSS内で画像を参照するときは /skin-media/ファイル名 を使います。","下载":"ダウンロード","下载清晰度":"ダウンロード画質","主题":"テーマ","也推手动保存":"手動保存も通知","仅支持 wav/mp3":"wav/mp3 のみ対応","仅限本人访问":"本人限定アクセス","今天":"今日","位置":"位置","使用中":"使用中","侦测到物体移动":"物体の動きを検知","侦测到碰撞/震动":"衝突・振動を検知","保存失败":"保存に失敗しました","保存设置":"設定を保存","修改密码":"パスワード変更","全选":"すべて選択","全部":"すべて","全部日期":"すべての日付","再输一次新密码":"新しいパスワードを再入力","删除":"削除","刹车":"ブレーキ","刹车占比":"ブレーキ割合","前视":"前方","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"更新時はGitHubと複数のミラーから最新版を並行して取得し、本体のファイルを上書きしてサービスを自動再起動します（約5秒）。設定・録画・ログイン情報は影響を受けません。","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"半透明の1行：時刻/位置/車速/ギア/アクセル/ブレーキ。映像をほぼ遮りません","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"1つ選択時は元ファイルをそのままダウンロード（ウォーターマーク付きは本機で合成）。複数選択時は1つのマルチ画面動画に合成します。ダウンロードとまとめは選択した画質で書き出します（初回は変換待ち）。","原因":"原因","原画":"オリジナル","发送测试":"テスト送信","叠加行车信息水印":"走行データのウォーターマークを重ねる","右B柱":"右Bピラー","右前翼":"右前フェンダー","右翼子板":"右フェンダー","合成视频":"動画を合成","同步状态":"同期状態","后视":"後方","启用推送":"通知を有効化","哨兵告警":"セントリー警告","哨兵模式触发":"セントリーモード作動","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"セントリー作動時に時刻・位置・原因・トリガー映像をFeishuへ送信します。既定は自建アプリの個人チャット（本人のみ閲覧可）で、グループボットのwebhook（グループ全員が閲覧可）にも切り替えられます。動画通知は個人チャットのみ対応です。","哨兵警觉":"セントリー警戒","回滚到上一版本":"前のバージョンに戻す","天":"日","实时同步中":"リアルタイム同期中","密码":"パスワード","尝试次数过多，请 5 分钟后再试":"試行回数が多すぎます。5分後にもう一度お試しください","展开详细数据 ⌄":"詳細データを開く ⌄","左B柱":"左Bピラー","左前翼":"左前フェンダー","左翼子板":"左フェンダー","已保存":"保存しました","已删除":"削除しました","已加入队列, 归档同步时写入车机U盘":"キューに追加しました。保存同期時に車載USBへ書き込みます","已同步":"同期済み","已是最新":"最新です","延迟推送":"通知の遅延","当前密码":"現在のパスワード","当前版本":"現在のバージョン","恢复车机导出":"車載機のエクスポートを再開","手动保存":"手動保存","打包zip":"zipにまとめる","按喇叭保存":"ホーンで保存","换个日期或点「全部」查看本分类所有事件":"日付を変えるか「すべて」をタップすると、この分類の全イベントを表示します","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"受信者：あなたの open_id（ou_ で始まる）またはFeishuのログインメール","推送内容：封面卡片（触发那帧图 + 信息，最快）":"通知内容：カバーカード（トリガー時のフレーム＋情報、最速）","推送内容：触发摄像头视频（可回放）":"通知内容：トリガーカメラの動画（再生可）","摄像头":"カメラ","收起详细数据 ⌃":"詳細データを閉じる ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"変更後は本機が新しいパスワードで自動再ログインし、スマホや他の端末のログイン状態は即座に無効になります。","敏感信息已明文显示，注意别外发":"機密情報を表示中。外部に送らないでください","敏感信息已隐藏（截图安全）":"機密情報は非表示です（スクショ安全）","新密码 (至少 6 位)":"新しいパスワード（6文字以上）","方向盘角度":"ステアリング角度","日期":"日付","时间":"時刻","昨天":"昨日","暂无归档视频":"保存された動画はありません","暂无皮肤, 使用默认外观":"スキンなし、既定の外観を使用","最新版本":"最新バージョン","有新版本":"新しいバージョンあり","未标注原因":"原因の記録なし","未检查":"未確認","条":"件","查看链接前缀 (可选): http://你的域名:端口":"閲覧リンクの接頭辞（任意）: http://あなたのドメイン:ポート","检查中…":"確認中…","检查更新":"更新を確認","正在上传…":"アップロード中…","正在下载并校验新版本…":"新バージョンをダウンロードして検証中…","正在合成多画面视频":"マルチ画面動画を合成中","活动ID":"イベントID","浅色":"ライト","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"車載USB（Teslaが書き込むミラー）を閲覧・管理します。接続すると車載機の読み取りが数秒中断され、5分間操作がないと自動で復帰します。","深色":"ダーク","清空":"クリア","点击行车记录仪图标":"ドラレコアイコンをタップ","片段":"クリップ","用户名":"ユーザー名","用户名或密码不对":"ユーザー名またはパスワードが違います","画面":"映像","界面语言，保存在本机浏览器":"表示言語はこのブラウザに保存されます","登录":"ログイン","登录状态保留":"ログイン状態の保持","盒子归档存储":"ボックス保存領域","确定要退出登录吗？":"ログアウトしますか？","私聊推送 — 只有我本人能看到":"個人チャット通知 — 本人だけが閲覧可","秒 (等片段写完再推)":"秒（クリップ完成後に通知）","秒（从触发前 5 秒开始截）":"秒（トリガー5秒前から記録）","立即升级":"今すぐ更新","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"グループボットの webhook（グループ通知のみ必要）: https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"グループボット — グループ全員が閲覧可","自动":"自動","行车记录":"ドライブレコーダー","行车记录仪菜单保存":"ドラレコメニューから保存","视频时长":"動画の長さ","视频清晰度：360p（约 1.8MB / 30 秒）":"動画画質：360p（約1.8MB / 30秒）","视频清晰度：480p（约 3MB / 30 秒）":"動画画質：480p（約3MB / 30秒）","视频清晰度：720p（约 5MB / 30 秒）":"動画画質：720p（約5MB / 30秒）","触发原因":"トリガー原因","设为锁车音":"ロック音に設定","设备正在拼接各路画面, 请勿关闭页面":"各カメラの映像を結合しています。ページを閉じないでください","设置":"設定","该筛选下暂无事件":"この条件のイベントはありません","跳转":"移動","车机U盘未连接":"車載USB未接続","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"車載機で録画されると、保存同期が自動で動画を取り込みます（ミラーは10分の待機が必要）","车机要求单个音频 <1MB, 请裁剪后重试":"車載機の制限で1ファイル<1MBです。トリミングして再試行してください","车载U盘存储":"車載USBストレージ","车门拉动":"ドアを引かれた","车门被拉动":"ドアが引かれました","载入中…":"読み込み中…","连接车机U盘":"車載USBに接続","退出登录":"ログアウト","选择日期…":"日付を選択…","选择要导出的画面":"書き出す映像を選択","选择语言":"言語を選択","遭遇袭击":"襲撃を検知","遭遇袭击 (紧急报警)":"襲撃を検知（緊急通報）","锁车音":"ロック音","锁车音必须是 WAV (mp3 只能当喇叭音效)":"ロック音はWAVのみ（mp3はホーン音としてのみ使用可）","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"ロック音はWAV 44.1kHz 16-bit <1MB 1〜5秒。LockChime.wav は車載USBのルートに、その他の mp3/wav は Boombox 音效として Boombox/ ディレクトリに書き込みます。ファイルは保存同期時に車載USBへ書き込まれます。","门把手被拉动":"ドアノブが引かれました","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ 現在のフォルダにファイルをアップロード (任意の種類, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ スキンファイルをアップロード (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ 音声をアップロード (wav/mp3, <1MB)","🌐 语言":"🌐 言語","🎨 网页背景":"🎨 スキン","👁 显示":"👁 表示","📂 文件":"📂 ファイル","🔄 更新":"🔄 アップデート","🔊 锁车语音":"🔊 ロック音","🔑 账号":"🔑 アカウント","🔔 推送":"🔔 通知","🗄 归档区":"🗄 保存領域","🙈 隐藏":"🙈 隠す","🚗 特斯拉哨兵查看器":"🚗 テスラ・セントリービューア","🚗 车机U盘":"🚗 車載USB","归档":"保存領域","待确认":"確認待ち","同步服务未运行":"同期サービスが停止中","路画面":"画面","最近一次":"最新","未启用":"無効","已启用私聊推送, 等下一个哨兵事件":"個人チャット通知を有効にしました。次のセントリーイベントを待機中","已启用, 但接收人/应用凭据未配全":"有効ですが、受信者またはアプリ資格情報が未設定です","读取设置失败":"設定の読み込みに失敗しました","私聊需要 App ID / Secret + 接收人":"個人チャットには App ID / Secret と受信者が必要です","请先填群机器人 webhook":"先にグループボットの webhook を入力してください","保存中…":"保存中…","发送失败":"送信に失敗しました","正在发送测试卡片…":"テストカードを送信中…","当前未开启登录保护, 设置密码后即开启":"現在ログイン保護は無効です。パスワードを設定すると有効になります","新密码至少 6 位":"新しいパスワードは6文字以上","两次输入的新密码不一致":"新しいパスワードが一致しません","提交中…":"送信中…","空目录":"空のフォルダ","正在连接 GitHub…":"GitHub に接続中…","检查失败":"確認に失敗しました","连不上 GitHub":"GitHub に接続できません","(可稍后重试)":"(後で再試行できます)","来源":"ソース","升级失败":"アップデートに失敗しました","原密码不对":"現在のパスワードが違います","修改失败":"変更に失敗しました","上传失败":"アップロードに失敗しました","密码已更新, 其它设备需要重新登录":"パスワードを更新しました。他の端末では再ログインが必要です","仅支持 css/jpg/png/webp":"css/jpg/png/webp のみ対応","皮肤已上传并应用":"スキンをアップロードして適用しました","文件超过 100MB 上限":"ファイルが上限の100MBを超えています","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"車載USBメモリを接続しました (車載機の読み取りを一時停止中。5分間操作がないと自動復帰)","已恢复车机导出":"車載機のエクスポートを復帰しました","车机U盘未连接, 请先点「连接车机U盘」":"車載USBメモリが未接続です。先に「車載USBメモリに接続」を押してください","归档里没有可用的片段":"保存領域に使用できるクリップがありません","请至少选择一路":"少なくとも1画面を選択してください","已有合成在进行":"合成を実行中です","合成失败":"合成に失敗しました","已开始下载":"ダウンロードを開始しました","此浏览器不支持本机合成, 请使用打包下载":"このブラウザは本機合成に非対応です。まとめてダウンロードしてください","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"合成完了","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"ko":{"App ID — cli_ 开头":"App ID — cli_ 로 시작","App Secret (已保存则留空表示不修改)":"App Secret (저장된 경우 비워두면 변경 안 함)","‹ 返回":"‹ 뒤로","⏳ 待写入车机U盘:":"⏳ 차량 USB 기록 대기:","▶ 轻触开始同步播放":"▶ 눌러서 동기화 재생 시작","⚙ 个性化":"⚙ 개인 설정","✅ 已写入:":"✅ 기록 완료:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"CSS 스킨이나 배경 이미지(jpg/png/webp)를 업로드하세요. CSS에서 이미지를 참조할 때는 /skin-media/파일명을 사용하세요.","下载":"다운로드","下载清晰度":"다운로드 화질","主题":"테마","也推手动保存":"수동 저장도 알림","仅支持 wav/mp3":"wav/mp3만 지원","仅限本人访问":"본인만 접근 가능","今天":"오늘","位置":"위치","使用中":"사용 중","侦测到物体移动":"물체 움직임 감지","侦测到碰撞/震动":"충돌/진동 감지","保存失败":"저장 실패","保存设置":"설정 저장","修改密码":"비밀번호 변경","全选":"전체 선택","全部":"전체","全部日期":"모든 날짜","再输一次新密码":"새 비밀번호 다시 입력","删除":"삭제","刹车":"브레이크","刹车占比":"브레이크 비율","前视":"전방","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"업데이트 시 GitHub와 여러 미러에서 최신 프로그램을 동시에 받아 본체 파일을 덮어쓰고 서비스를 자동 재시작합니다 (약 5초). 설정, 녹화, 로그인 정보는 영향받지 않습니다.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"반투명 한 줄: 시간/위치/속도/기어/액셀/브레이크, 화면을 거의 가리지 않음","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"하나 선택 시 원본 파일을 바로 다운로드(워터마크 시 본체에서 합성), 여러 개 선택 시 하나의 다중 화면 영상으로 합성합니다. 다운로드와 묶기는 선택한 화질로 내보냅니다(처음엔 변환 대기 필요).","原因":"원인","原画":"원본","发送测试":"테스트 전송","叠加行车信息水印":"주행 정보 워터마크 표시","右B柱":"우측 B필러","右前翼":"우측 전면 펜더","右翼子板":"우측 펜더","合成视频":"영상 합성","同步状态":"동기화 상태","后视":"후방","启用推送":"알림 사용","哨兵告警":"센트리 경보","哨兵模式触发":"센트리 모드 작동","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"센트리 작동 시 시간/위치/트리거 원인과 트리거 화면을 Feishu로 보냅니다. 기본은 자체 앱 개인 채팅(본인만 확인 가능)이며, 그룹 봇 webhook(그룹원 모두 확인 가능)으로 전환할 수 있습니다. 영상 알림은 개인 채팅만 지원합니다.","哨兵警觉":"센트리 주의","回滚到上一版本":"이전 버전으로 롤백","天":"일","实时同步中":"실시간 동기화 중","密码":"비밀번호","尝试次数过多，请 5 分钟后再试":"시도 횟수가 너무 많습니다. 5분 후 다시 시도하세요","展开详细数据 ⌄":"상세 데이터 펼치기 ⌄","左B柱":"좌측 B필러","左前翼":"좌측 전면 펜더","左翼子板":"좌측 펜더","已保存":"저장됨","已删除":"삭제됨","已加入队列, 归档同步时写入车机U盘":"대기열에 추가됨, 보관 동기화 시 차량 USB에 기록","已同步":"동기화됨","已是最新":"최신 버전","延迟推送":"알림 지연","当前密码":"현재 비밀번호","当前版本":"현재 버전","恢复车机导出":"차량 내보내기 복구","手动保存":"수동 저장","打包zip":"zip으로 묶기","按喇叭保存":"경적을 눌러 저장","换个日期或点「全部」查看本分类所有事件":"날짜를 바꾸거나 「전체」를 눌러 이 분류의 모든 이벤트를 보세요","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"수신자: 내 open_id (ou_ 로 시작) 또는 Feishu 로그인 이메일","推送内容：封面卡片（触发那帧图 + 信息，最快）":"알림 내용: 커버 카드 (트리거 순간 이미지 + 정보, 가장 빠름)","推送内容：触发摄像头视频（可回放）":"알림 내용: 트리거 카메라 영상 (재생 가능)","摄像头":"카메라","收起详细数据 ⌃":"상세 데이터 접기 ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"변경 후 본체는 새 비밀번호로 자동 재로그인되며, 휴대폰과 다른 기기의 로그인 상태는 즉시 만료됩니다.","敏感信息已明文显示，注意别外发":"민감 정보가 표시됩니다. 외부 유출 주의","敏感信息已隐藏（截图安全）":"민감 정보가 가려졌습니다 (캡처 안전)","新密码 (至少 6 位)":"새 비밀번호 (6자 이상)","方向盘角度":"핸들 각도","日期":"날짜","时间":"시간","昨天":"어제","暂无归档视频":"보관된 영상이 없습니다","暂无皮肤, 使用默认外观":"스킨 없음, 기본 화면 사용","最新版本":"최신 버전","有新版本":"새 버전 있음","未标注原因":"원인 미표시","未检查":"확인 안 함","条":"개","查看链接前缀 (可选): http://你的域名:端口":"조회 링크 접두사 (선택): http://내-도메인:포트","检查中…":"확인 중…","检查更新":"업데이트 확인","正在上传…":"업로드 중…","正在下载并校验新版本…":"새 버전 다운로드 및 검증 중…","正在合成多画面视频":"다중 화면 영상 합성 중","活动ID":"이벤트 ID","浅色":"라이트","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"차량 USB(Tesla가 기록하는 미러)를 보고 관리합니다. 연결하면 차량 읽기가 몇 초간 끊기고, 5분간 조작이 없으면 자동 복구됩니다.","深色":"다크","清空":"초기화","点击行车记录仪图标":"블랙박스 아이콘 터치","片段":"클립","用户名":"사용자 이름","用户名或密码不对":"사용자 이름 또는 비밀번호가 틀렸습니다","画面":"화면","界面语言，保存在本机浏览器":"인터페이스 언어는 이 브라우저에 저장됩니다","登录":"로그인","登录状态保留":"로그인 상태 유지","盒子归档存储":"박스 보관 저장공간","确定要退出登录吗？":"로그아웃할까요?","私聊推送 — 只有我本人能看到":"개인 채팅 알림 — 본인만 볼 수 있음","秒 (等片段写完再推)":"초 (클립 저장 후 알림)","秒（从触发前 5 秒开始截）":"초 (트리거 5초 전부터 녹화)","立即升级":"지금 업데이트","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"그룹 봇 webhook (그룹 알림에만 필요): https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"그룹 봇 — 그룹원 모두 볼 수 있음","自动":"자동","行车记录":"주행 기록","行车记录仪菜单保存":"블랙박스 메뉴에서 저장","视频时长":"영상 길이","视频清晰度：360p（约 1.8MB / 30 秒）":"영상 화질: 360p (약 1.8MB / 30초)","视频清晰度：480p（约 3MB / 30 秒）":"영상 화질: 480p (약 3MB / 30초)","视频清晰度：720p（约 5MB / 30 秒）":"영상 화질: 720p (약 5MB / 30초)","触发原因":"트리거 원인","设为锁车音":"잠금음으로 설정","设备正在拼接各路画面, 请勿关闭页面":"각 화면을 이어 붙이는 중입니다. 페이지를 닫지 마세요","设置":"설정","该筛选下暂无事件":"이 필터에 이벤트가 없습니다","跳转":"이동","车机U盘未连接":"차량 USB 연결 안 됨","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"차량에서 녹화하면 보관 동기화 작업이 자동으로 영상을 가져옵니다 (미러는 10분간 유휴 필요)","车机要求单个音频 <1MB, 请裁剪后重试":"차량 요구사항으로 파일당 <1MB입니다. 잘라서 다시 시도하세요","车载U盘存储":"차량 USB 저장공간","车门拉动":"문 당김","车门被拉动":"문이 당겨졌습니다","载入中…":"불러오는 중…","连接车机U盘":"차량 USB 연결","退出登录":"로그아웃","选择日期…":"날짜 선택…","选择要导出的画面":"내보낼 화면 선택","选择语言":"언어 선택","遭遇袭击":"차량 습격 감지","遭遇袭击 (紧急报警)":"차량 습격 감지 (긴급 경보)","锁车音":"잠금음","锁车音必须是 WAV (mp3 只能当喇叭音效)":"잠금음은 WAV만 가능 (mp3는 경적음으로만 사용)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"잠금음은 WAV 44.1kHz 16-bit <1MB 1~5초가 필요합니다. LockChime.wav는 차량 USB 루트에, 그 외 mp3/wav는 Boombox 음향으로 Boombox/ 폴더에 기록됩니다. 파일은 보관 동기화 시 차량 USB에 기록됩니다.","门把手被拉动":"도어 손잡이가 당겨졌습니다","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ 현재 폴더로 파일 업로드 (모든 형식, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ 스킨 파일 업로드 (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ 오디오 업로드 (wav/mp3, <1MB)","🌐 语言":"🌐 언어","🎨 网页背景":"🎨 스킨","👁 显示":"👁 표시","📂 文件":"📂 파일","🔄 更新":"🔄 업데이트","🔊 锁车语音":"🔊 잠금 사운드","🔑 账号":"🔑 계정","🔔 推送":"🔔 알림","🗄 归档区":"🗄 보관함","🙈 隐藏":"🙈 숨기기","🚗 特斯拉哨兵查看器":"🚗 테슬라 센트리 뷰어","🚗 车机U盘":"🚗 차량 USB","归档":"보관함","待确认":"확인 대기","同步服务未运行":"동기화 서비스가 실행되지 않음","路画面":"화면","最近一次":"최근","未启用":"사용 안 함","已启用私聊推送, 等下一个哨兵事件":"개인 채팅 알림이 켜졌습니다. 다음 센트리 이벤트를 기다리는 중","已启用, 但接收人/应用凭据未配全":"사용 중이지만 수신자/앱 자격 정보가 모두 설정되지 않았습니다","读取设置失败":"설정을 읽지 못했습니다","私聊需要 App ID / Secret + 接收人":"개인 채팅에는 App ID / Secret 과 수신자가 필요합니다","请先填群机器人 webhook":"먼저 그룹 봇 webhook을 입력하세요","保存中…":"저장 중…","发送失败":"전송 실패","正在发送测试卡片…":"테스트 카드 전송 중…","当前未开启登录保护, 设置密码后即开启":"현재 로그인 보호가 꺼져 있습니다. 비밀번호를 설정하면 켜집니다","新密码至少 6 位":"새 비밀번호는 6자 이상","两次输入的新密码不一致":"새 비밀번호가 일치하지 않습니다","提交中…":"제출 중…","空目录":"빈 폴더","正在连接 GitHub…":"GitHub에 연결 중…","检查失败":"확인 실패","连不上 GitHub":"GitHub에 연결할 수 없음","(可稍后重试)":"(나중에 다시 시도할 수 있습니다)","来源":"출처","升级失败":"업데이트 실패","原密码不对":"현재 비밀번호가 틀렸습니다","修改失败":"변경 실패","上传失败":"업로드 실패","密码已更新, 其它设备需要重新登录":"비밀번호가 변경되었습니다. 다른 기기에서는 다시 로그인해야 합니다","仅支持 css/jpg/png/webp":"css/jpg/png/webp만 지원","皮肤已上传并应用":"스킨을 업로드하고 적용했습니다","文件超过 100MB 上限":"파일이 100MB 한도를 초과했습니다","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"차량 USB 메모리가 연결되었습니다 (차량 읽기를 일시 중단, 5분간 조작이 없으면 자동 복구)","已恢复车机导出":"차량 내보내기를 복구했습니다","车机U盘未连接, 请先点「连接车机U盘」":"차량 USB 메모리가 연결되지 않았습니다. 먼저 「차량 USB 메모리 연결」을 누르세요","归档里没有可用的片段":"보관함에 사용할 수 있는 클립이 없습니다","请至少选择一路":"화면을 하나 이상 선택하세요","已有合成在进行":"이미 합성 중입니다","合成失败":"합성 실패","已开始下载":"다운로드를 시작했습니다","此浏览器不支持本机合成, 请使用打包下载":"이 브라우저는 본체 합성을 지원하지 않습니다. 묶음 다운로드를 사용하세요","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"합성 완료","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"nl":{"App ID — cli_ 开头":"App ID — begint met cli_","App Secret (已保存则留空表示不修改)":"App Secret (leeg laten om ongewijzigd te houden)","‹ 返回":"‹ Terug","⏳ 待写入车机U盘:":"⏳ Nog naar de USB van de auto te schrijven:","▶ 轻触开始同步播放":"▶ Tik om synchroon af te spelen","⚙ 个性化":"⚙ Personalisatie","✅ 已写入:":"✅ Geschreven:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"Upload een CSS-skinbestand of achtergrondafbeelding (jpg/png/webp). Verwijs naar afbeeldingen in CSS via /skin-media/bestandsnaam.","下载":"Downloaden","下载清晰度":"Downloadkwaliteit","主题":"Thema","也推手动保存":"Ook handmatige opnames","仅支持 wav/mp3":"Alleen wav/mp3","仅限本人访问":"Alleen toegankelijk voor jou","今天":"Vandaag","位置":"Locatie","使用中":"In gebruik","侦测到物体移动":"Beweging gedetecteerd","侦测到碰撞/震动":"Botsing/schok gedetecteerd","保存失败":"Opslaan mislukt","保存设置":"Instellingen opslaan","修改密码":"Wachtwoord wijzigen","全选":"Alles selecteren","全部":"Alles","全部日期":"Alle datums","再输一次新密码":"Herhaal het nieuwe wachtwoord","删除":"Verwijderen","刹车":"Rem","刹车占比":"Rempercentage","前视":"Voor","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"De update haalt de nieuwste versie parallel op van GitHub en meerdere mirrors, overschrijft de lokale bestanden en herstart de service (circa 5 s); instellingen, opnames en inloggegevens blijven behouden.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"Eén semi-transparante regel: tijd/locatie/snelheid/versnelling/gas/rem, nauwelijks zichtbaar over het beeld","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"Bij één selectie download je het originele bestand (met watermerk wordt het lokaal samengesteld); bij meerdere selecties wordt één multi-beeldvideo gemaakt. Downloaden en zippen gebeurt in de gekozen kwaliteit (de eerste keer moet er worden getranscodeerd).","原因":"Reden","原画":"Origineel","发送测试":"Test verzenden","叠加行车信息水印":"Rijgegevens als watermerk toevoegen","右B柱":"B-stijl rechts","右前翼":"Voorspatbord rechts","右翼子板":"Spatbord rechts","合成视频":"Video samenvoegen","同步状态":"Synchronisatiestatus","后视":"Achter","启用推送":"Meldingen inschakelen","哨兵告警":"Sentry-meldingen","哨兵模式触发":"Sentry geactiveerd","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"Bij een Sentry-trigger worden tijd/locatie/oorzaak en het triggerbeeld naar Feishu gestuurd. Standaard via de eigen app in privé (alleen jij ziet het); je kunt ook de webhook van een groepsbot gebruiken (iedereen in de groep ziet het). Video-push kan alleen privé.","哨兵警觉":"Sentry-alert","回滚到上一版本":"Terug naar vorige versie","天":"dagen","实时同步中":"Live synchroniseren","密码":"Wachtwoord","尝试次数过多，请 5 分钟后再试":"Te veel pogingen, probeer het over 5 minuten","展开详细数据 ⌄":"Details tonen ⌄","左B柱":"B-stijl links","左前翼":"Voorspatbord links","左翼子板":"Spatbord links","已保存":"Opgeslagen","已删除":"Verwijderd","已加入队列, 归档同步时写入车机U盘":"In de wachtrij, wordt bij de archiefsynchronisatie naar de USB van de auto geschreven","已同步":"Gesynchroniseerd","已是最新":"Al up-to-date","延迟推送":"Vertraagde melding","当前密码":"Huidig wachtwoord","当前版本":"Huidige versie","恢复车机导出":"Toegang auto herstellen","手动保存":"Handmatige opnames","打包zip":"zip maken","按喇叭保存":"Opgeslagen met claxon","换个日期或点「全部」查看本分类所有事件":"Kies een andere datum of tik op 'Alles' om alle gebeurtenissen van deze categorie te zien","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"Ontvanger: je open_id (begint met ou_) of je Feishu-inlogmail","推送内容：封面卡片（触发那帧图 + 信息，最快）":"Inhoud: omslagkaart (beeld van de trigger + info, het snelst)","推送内容：触发摄像头视频（可回放）":"Inhoud: video van de camera die triggerde (afspeelbaar)","摄像头":"Camera","收起详细数据 ⌃":"Details verbergen ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"Na de wijziging logt dit apparaat automatisch opnieuw in met het nieuwe wachtwoord; sessies op je telefoon en andere apparaten vervallen direct.","敏感信息已明文显示，注意别外发":"Gevoelige gegevens zichtbaar, deel ze niet","敏感信息已隐藏（截图安全）":"Gevoelige gegevens verborgen (veilig voor screenshots)","新密码 (至少 6 位)":"Nieuw wachtwoord (minimaal 6 tekens)","方向盘角度":"Stuurhoek","日期":"Datum","时间":"Tijd","昨天":"Gisteren","暂无归档视频":"Geen gearchiveerde video's","暂无皮肤, 使用默认外观":"Geen skins, standaarduiterlijk","最新版本":"Nieuwste versie","有新版本":"Nieuwe versie beschikbaar","未标注原因":"Geen reden opgegeven","未检查":"Niet gecontroleerd","条":"clips","查看链接前缀 (可选): http://你的域名:端口":"Voorvoegsel weergavelink (optioneel): http://jouwdomein:poort","检查中…":"Controleren…","检查更新":"Controleren op updates","正在上传…":"Uploaden…","正在下载并校验新版本…":"Nieuwe versie downloaden en verifiëren…","正在合成多画面视频":"Multi-beeldvideo wordt gemaakt","活动ID":"Gebeurtenis-ID","浅色":"Licht","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"Bekijk en beheer de USB van de auto (de kopie die Tesla schrijft). Bij verbinden verliest de auto enkele seconden de toegang; na 5 minuten zonder activiteit wordt dit hersteld.","深色":"Donker","清空":"Selectie opheffen","点击行车记录仪图标":"Dashcam-pictogram aangetikt","片段":"Clips","用户名":"Gebruikersnaam","用户名或密码不对":"Gebruikersnaam of wachtwoord onjuist","画面":"Beelden","界面语言，保存在本机浏览器":"Taal van de interface, opgeslagen in deze browser","登录":"Inloggen","登录状态保留":"Ingelogd blijven","盒子归档存储":"Archiefopslag box","确定要退出登录吗？":"Weet je zeker dat je wilt uitloggen?","私聊推送 — 只有我本人能看到":"Privébericht — alleen jij kunt het zien","秒 (等片段写完再推)":"seconden (wacht tot de clips zijn geschreven)","秒（从触发前 5 秒开始截）":"seconden (vanaf 5 s voor de trigger)","立即升级":"Nu bijwerken","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"webhook van groepsbot (alleen nodig voor groepspush): https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"Groepsbot — iedereen in de groep kan het zien","自动":"Automatisch","行车记录":"Dashcam","行车记录仪菜单保存":"Opgeslagen via Dashcam-menu","视频时长":"Duur van de video","视频清晰度：360p（约 1.8MB / 30 秒）":"Videokwaliteit: 360p (circa 1.8MB / 30 s)","视频清晰度：480p（约 3MB / 30 秒）":"Videokwaliteit: 480p (circa 3MB / 30 s)","视频清晰度：720p（约 5MB / 30 秒）":"Videokwaliteit: 720p (circa 5MB / 30 s)","触发原因":"Oorzaak activatie","设为锁车音":"Instellen als vergrendelgeluid","设备正在拼接各路画面, 请勿关闭页面":"Het apparaat voegt de beelden samen, sluit de pagina niet","设置":"Instellingen","该筛选下暂无事件":"Geen gebeurtenissen met dit filter","跳转":"Gaan","车机U盘未连接":"USB van de auto niet verbonden","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"Na opname in de auto neemt de archiefsynchronisatie de video's automatisch over (de mirror moet 10 minuten inactief zijn)","车机要求单个音频 <1MB, 请裁剪后重试":"De auto vereist audio <1MB, knip het bestand en probeer opnieuw","车载U盘存储":"USB-opslag auto","车门拉动":"Portier geforceerd","车门被拉动":"Portier opengetrokken","载入中…":"Laden…","连接车机U盘":"USB van de auto verbinden","退出登录":"Uitloggen","选择日期…":"Kies datum…","选择要导出的画面":"Kies de beelden om te exporteren","选择语言":"Taal kiezen","遭遇袭击":"Aanval","遭遇袭击 (紧急报警)":"Aanval (noodalarm)","锁车音":"Vergrendelgeluid","锁车音必须是 WAV (mp3 只能当喇叭音效)":"Vergrendelgeluid moet WAV zijn (mp3 alleen als claxongeluid)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"Vergrendelgeluid vereist WAV 44.1kHz 16-bit <1MB 1~5 s; LockChime.wav wordt in de hoofdmap van de USB van de auto gezet, overige mp3/wav als Boombox-geluiden in de map Boombox/. Bestanden worden bij de archiefsynchronisatie naar de USB van de auto geschreven.","门把手被拉动":"Deurgreep opengetrokken","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ Bestand uploaden naar huidige map (elk type, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ Skinbestand uploaden (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ Audio uploaden (wav/mp3, <1MB)","🌐 语言":"🌐 Taal","🎨 网页背景":"🎨 Uiterlijk","👁 显示":"👁 Tonen","📂 文件":"📂 Bestanden","🔄 更新":"🔄 Updates","🔊 锁车语音":"🔊 Vergrendelgeluid","🔑 账号":"🔑 Account","🔔 推送":"🔔 Meldingen","🗄 归档区":"🗄 Archief","🙈 隐藏":"🙈 Verbergen","🚗 特斯拉哨兵查看器":"🚗 Tesla Sentry-viewer","🚗 车机U盘":"🚗 USB van de auto","归档":"Archief","待确认":"Te bevestigen","同步服务未运行":"Synchronisatieservice niet actief","路画面":"Kanaalbeeld","最近一次":"Laatste keer","未启用":"Niet ingeschakeld","已启用私聊推送, 等下一个哨兵事件":"Privéberichten ingeschakeld, wacht op de volgende Sentinelle-gebeurtenis","已启用, 但接收人/应用凭据未配全":"Ingeschakeld, maar ontvangers/app-referenties ontbreken","读取设置失败":"Instellingen lezen mislukt","私聊需要 App ID / Secret + 接收人":"Privéberichten vereisen App ID / Secret + ontvanger","请先填群机器人 webhook":"Vul eerst de webhook van de groepsbot in","保存中…":"Opslaan…","发送失败":"Verzenden mislukt","正在发送测试卡片…":"Testkaart wordt verzonden…","当前未开启登录保护, 设置密码后即开启":"Toegangsbeveiliging uit: wordt ingeschakeld zodra je een wachtwoord instelt","新密码至少 6 位":"Nieuw wachtwoord moet minstens 6 tekens hebben","两次输入的新密码不一致":"De nieuwe wachtwoorden komen niet overeen","提交中…":"Bezig met verzenden…","空目录":"Lege map","正在连接 GitHub…":"Verbinden met GitHub…","检查失败":"Controle mislukt","连不上 GitHub":"Kan geen verbinding maken met GitHub","(可稍后重试)":"(probeer het later opnieuw)","来源":"Bron","升级失败":"Upgraden mislukt","原密码不对":"Huidig wachtwoord onjuist","修改失败":"Wijzigen mislukt","上传失败":"Uploaden mislukt","密码已更新, 其它设备需要重新登录":"Wachtwoord bijgewerkt, andere apparaten moeten opnieuw inloggen","仅支持 css/jpg/png/webp":"Alleen css/jpg/png/webp","皮肤已上传并应用":"Thema geüpload en toegepast","文件超过 100MB 上限":"Bestand groter dan de limiet van 100MB","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"USB-stick van de auto verbonden (lezen door de auto tijdelijk gestopt, automatisch hersteld na 5 minuten inactiviteit)","已恢复车机导出":"Export vanaf de auto hersteld","车机U盘未连接, 请先点「连接车机U盘」":"USB-stick van de auto niet verbonden, tik eerst op 'USB-stick auto verbinden'","归档里没有可用的片段":"Geen bruikbare clips in het archief","请至少选择一路":"Selecteer minstens één kanaal","已有合成在进行":"Er loopt al een samenvoeging","合成失败":"Samenvoegen mislukt","已开始下载":"Download gestart","此浏览器不支持本机合成, 请使用打包下载":"Deze browser ondersteunt lokaal samenvoegen niet, gebruik de download als pakket","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"Samenvoegen klaar","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}},"ru":{"App ID — cli_ 开头":"App ID — начинается с cli_","App Secret (已保存则留空表示不修改)":"App Secret (оставьте пустым, чтобы не менять)","‹ 返回":"‹ Назад","⏳ 待写入车机U盘:":"⏳ Ожидают записи на USB авто:","▶ 轻触开始同步播放":"▶ Коснитесь для синхронного воспроизведения","⚙ 个性化":"⚙ Персонализация","✅ 已写入:":"✅ Записано:","上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。":"Загрузите CSS-скин или фоновое изображение (jpg/png/webp). В CSS ссылайтесь на картинки как /skin-media/имя файла.","下载":"Скачать","下载清晰度":"Качество скачивания","主题":"Тема","也推手动保存":"И ручные сохранения","仅支持 wav/mp3":"Поддерживается только wav/mp3","仅限本人访问":"Доступ только для владельца","今天":"Сегодня","位置":"Место","使用中":"Используется","侦测到物体移动":"Обнаружено движение объекта","侦测到碰撞/震动":"Обнаружен удар или вибрация","保存失败":"Не удалось сохранить","保存设置":"Сохранить","修改密码":"Сменить пароль","全选":"Выбрать все","全部":"Все","全部日期":"Все даты","再输一次新密码":"Повторите новый пароль","删除":"Удалить","刹车":"Тормоз","刹车占比":"Доля торможения","前视":"Передняя","升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。":"При обновлении последняя версия скачивается с GitHub и нескольких зеркал одновременно, файлы на устройстве заменяются, сервис перезапускается (около 5 с); настройки, записи и данные входа не затрагиваются.","半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面":"Полупрозрачная строка: время/место/скорость/передача/газ/тормоз, почти не закрывает кадр","单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。":"Один файл скачивается как оригинал (с водяным знаком — сборка на устройстве); несколько — в одно многокамерное видео. Скачивание и архив идут в выбранном качестве (в первый раз нужно дождаться перекодирования).","原因":"Причина","原画":"Оригинал","发送测试":"Отправить тест","叠加行车信息水印":"Добавить водяной знак с данными","右B柱":"Правая стойка B","右前翼":"Правое переднее крыло","右翼子板":"Правое крыло","合成视频":"Объединить в видео","同步状态":"Статус синхронизации","后视":"Задняя","启用推送":"Включить уведомления","哨兵告警":"Оповещения Sentry","哨兵模式触发":"Сработал режим Sentry","哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。":"При срабатывании Sentry время, место, причина и кадр отправляются в Feishu. По умолчанию — личные сообщения приложения (видите только вы); можно переключиться на webhook бота чата (видят все в чате). Видео отправляется только в личные сообщения.","哨兵警觉":"Внимание Sentry","回滚到上一版本":"Откатить к прошлой версии","天":"дн.","实时同步中":"Синхронизация в реальном времени","密码":"Пароль","尝试次数过多，请 5 分钟后再试":"Слишком много попыток, повторите через 5 минут","展开详细数据 ⌄":"Развернуть данные ⌄","左B柱":"Левая стойка B","左前翼":"Левое переднее крыло","左翼子板":"Левое крыло","已保存":"Сохранено","已删除":"Удалено","已加入队列, 归档同步时写入车机U盘":"Добавлено в очередь, запись на USB авто при синхронизации","已同步":"Синхронизировано","已是最新":"Актуальная версия","延迟推送":"Задержка отправки","当前密码":"Текущий пароль","当前版本":"Текущая версия","恢复车机导出":"Вернуть доступ авто к USB","手动保存":"Сохранено вручную","打包zip":"Упаковать в zip","按喇叭保存":"Сохранение сигналом","换个日期或点「全部」查看本分类所有事件":"Смените дату или нажмите «Все», чтобы увидеть все события категории","接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱":"Получатель: ваш open_id (начинается с ou_) или почта входа Feishu","推送内容：封面卡片（触发那帧图 + 信息，最快）":"Содержание: карточка (кадр срабатывания + данные, быстрее всего)","推送内容：触发摄像头视频（可回放）":"Содержание: видео с камеры (с просмотром)","摄像头":"Камера","收起详细数据 ⌃":"Свернуть данные ⌃","改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。":"После изменения устройство автоматически войдёт с новым паролем, а сеансы на телефоне и других устройствах сразу завершатся.","敏感信息已明文显示，注意别外发":"Чувствительные данные открыты — не пересылайте","敏感信息已隐藏（截图安全）":"Чувствительные данные скрыты (безопасно для скриншотов)","新密码 (至少 6 位)":"Новый пароль (минимум 6 символов)","方向盘角度":"Угол руля","日期":"Дата","时间":"Время","昨天":"Вчера","暂无归档视频":"Архивных видео нет","暂无皮肤, 使用默认外观":"Скинов нет, используется стандартный вид","最新版本":"Последняя версия","有新版本":"Есть обновление","未标注原因":"Причина не указана","未检查":"Не проверено","条":"шт.","查看链接前缀 (可选): http://你的域名:端口":"Префикс ссылки (необязательно): http://ваш-домен:порт","检查中…":"Проверка…","检查更新":"Проверить обновления","正在上传…":"Идёт загрузка…","正在下载并校验新版本…":"Загрузка и проверка новой версии…","正在合成多画面视频":"Идёт сборка многокамерного видео","活动ID":"ID события","浅色":"Светлая","浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。":"Просмотр USB авто (зеркало, куда пишет Tesla). При подключении чтение в авто прерывается на несколько секунд, через 5 минут без действий доступ восстанавливается.","深色":"Тёмная","清空":"Очистить","点击行车记录仪图标":"Нажата иконка регистратора","片段":"Фрагментов","用户名":"Имя пользователя","用户名或密码不对":"Неверное имя пользователя или пароль","画面":"Камер","界面语言，保存在本机浏览器":"Язык интерфейса, хранится в этом браузере","登录":"Войти","登录状态保留":"Сеанс сохраняется","盒子归档存储":"Архив на боксе","确定要退出登录吗？":"Выйти из аккаунта?","私聊推送 — 只有我本人能看到":"Личные сообщения — видите только вы","秒 (等片段写完再推)":"с (сначала запись клипа, затем отправка)","秒（从触发前 5 秒开始截）":"с (запись начинается за 5 с до срабатывания)","立即升级":"Обновить сейчас","群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...":"webhook бота чата (только для группы): https://open.feishu.cn/open-apis/bot/v2/hook/...","群机器人 — 群里所有人都能看到":"Бот в чате — видят все участники","自动":"Авто","行车记录":"Дорожные записи","行车记录仪菜单保存":"Сохранение через меню регистратора","视频时长":"Длительность видео","视频清晰度：360p（约 1.8MB / 30 秒）":"Качество видео: 360p (около 1.8MB / 30 с)","视频清晰度：480p（约 3MB / 30 秒）":"Качество видео: 480p (около 3MB / 30 с)","视频清晰度：720p（约 5MB / 30 秒）":"Качество видео: 720p (около 5MB / 30 с)","触发原因":"Причина срабатывания","设为锁车音":"Сделать звуком закрытия","设备正在拼接各路画面, 请勿关闭页面":"Устройство склеивает камеры, не закрывайте страницу","设置":"Настройки","该筛选下暂无事件":"По этому фильтру событий нет","跳转":"Перейти","车机U盘未连接":"USB авто не подключён","车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)":"После записи в авто задача синхронизации сама заберёт видео (зеркалу нужно 10 минут простоя)","车机要求单个音频 <1MB, 请裁剪后重试":"Авто требует файл <1MB, обрежьте и повторите","车载U盘存储":"Хранилище USB в авто","车门拉动":"Дёргание двери","车门被拉动":"Дверь дёрнули","载入中…":"Загрузка…","连接车机U盘":"Подключить USB авто","退出登录":"Выйти","选择日期…":"Выбрать дату…","选择要导出的画面":"Выберите камеры для экспорта","选择语言":"Выбор языка","遭遇袭击":"Нападение на авто","遭遇袭击 (紧急报警)":"Нападение на авто (тревога)","锁车音":"Звук закрытия","锁车音必须是 WAV (mp3 只能当喇叭音效)":"Звук закрытия должен быть WAV (mp3 — только для Boombox)","锁车音要求 WAV 44.1kHz 16-bit <1MB 1~5秒；LockChime.wav 写入车机U盘根目录，其它 mp3/wav 作为 Boombox 音效写入 Boombox/ 目录。文件在归档同步时写入车机U盘。":"Звук закрытия: WAV 44.1kHz 16-bit <1MB 1–5 с; LockChime.wav записывается в корень USB авто, остальные mp3/wav — как звуки Boombox в папку Boombox/. Файлы записываются на USB авто при синхронизации архива.","门把手被拉动":"Дёрнули ручку двери","＋ 上传文件到当前目录 (任意类型, ≤100MB)":"＋ Загрузить файл в текущую папку (любой тип, ≤100MB)","＋ 上传皮肤文件 (css/jpg/png/webp)":"＋ Загрузить файл скина (css/jpg/png/webp)","＋ 上传音频 (wav/mp3, <1MB)":"＋ Загрузить аудио (wav/mp3, <1MB)","🌐 语言":"🌐 Язык","🎨 网页背景":"🎨 Оформление","👁 显示":"👁 Показать","📂 文件":"📂 Файлы","🔄 更新":"🔄 Обновление","🔊 锁车语音":"🔊 Звук закрытия","🔑 账号":"🔑 Аккаунт","🔔 推送":"🔔 Уведомления","🗄 归档区":"🗄 Архив","🙈 隐藏":"🙈 Скрыть","🚗 特斯拉哨兵查看器":"🚗 Просмотр записей Tesla Sentry","🚗 车机U盘":"🚗 USB в авто","归档":"Архив","待确认":"Ожидает подтверждения","同步服务未运行":"Служба синхронизации не запущена","路画面":"Камер","最近一次":"Последний раз","未启用":"Не включено","已启用私聊推送, 等下一个哨兵事件":"Личные уведомления включены, ждём следующее событие Sentry","已启用, 但接收人/应用凭据未配全":"Включено, но получатель или данные приложения заполнены не полностью","读取设置失败":"Не удалось прочитать настройки","私聊需要 App ID / Secret + 接收人":"Для личных сообщений нужны App ID / Secret и получатель","请先填群机器人 webhook":"Сначала укажите webhook бота чата","保存中…":"Сохранение…","发送失败":"Не удалось отправить","正在发送测试卡片…":"Отправка тестовой карточки…","当前未开启登录保护, 设置密码后即开启":"Защита входа выключена, включится после установки пароля","新密码至少 6 位":"Новый пароль — минимум 6 символов","两次输入的新密码不一致":"Новые пароли не совпадают","提交中…":"Отправка…","空目录":"Пустая папка","正在连接 GitHub…":"Подключение к GitHub…","检查失败":"Проверка не удалась","连不上 GitHub":"Нет связи с GitHub","(可稍后重试)":"(можно повторить позже)","来源":"Источник","升级失败":"Не удалось обновить","原密码不对":"Неверный текущий пароль","修改失败":"Не удалось изменить","上传失败":"Не удалось загрузить","密码已更新, 其它设备需要重新登录":"Пароль обновлён, на других устройствах нужен повторный вход","仅支持 css/jpg/png/webp":"Только css/jpg/png/webp","皮肤已上传并应用":"Скин загружен и применён","文件超过 100MB 上限":"Файл больше лимита 100MB","车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)":"USB-накопитель авто подключён (чтение в авто временно отключено, через 5 минут без действий доступ восстановится)","已恢复车机导出":"Доступ авто к USB восстановлен","车机U盘未连接, 请先点「连接车机U盘」":"USB-накопитель авто не подключён, сначала нажмите «Подключить USB авто»","归档里没有可用的片段":"В архиве нет доступных фрагментов","请至少选择一路":"Выберите хотя бы одну камеру","已有合成在进行":"Сборка уже идёт","合成失败":"Не удалось собрать","已开始下载":"Загрузка начата","此浏览器不支持本机合成, 请使用打包下载":"Браузер не поддерживает сборку на устройстве, используйте скачивание архивом","特斯拉哨兵云盘":"Tesla Sentry Drive","合成完成":"Готово","路":{"en":"cams","de":"Kam.","fr":"cams","it":"cam","es":"cams","nl":"cam.","ru":"кам.","ja":"カメラ","ko":"카메라"}}}

def tr(s, lang="en"):
    """把中文 UI 文案翻成 lang (查不到回落到英文, 再回落到原文)"""
    if not s or lang == "zh":
        return s
    m = LANG_TABLE.get(lang) or {}
    return m.get(s) or (LANG_TABLE.get("en") or {}).get(s) or s

def lang_from_accept(hdr, default="zh"):
    """从 Accept-Language 里挑一个我们支持的语言"""
    codes = [c for c, _ in LANG_NAMES]
    for part in (hdr or "").split(","):
        tag = part.split(";")[0].strip().lower()
        if not tag:
            continue
        if tag in codes:
            return tag
        two = tag.split("-")[0]
        if two in codes:
            return two
    return default

CAT_NAMES = {"SentryClips": "哨兵告警", "SavedClips": "手动保存", "RecentClips": "行车记录"}
# 注入页面用的 JSON (预生成, 避免每次请求都序列化)
I18N_JS = json.dumps(LANG_TABLE, ensure_ascii=False, separators=(",", ":"))
LANG_JS = json.dumps([[c, n] for c, n in LANG_NAMES], ensure_ascii=False, separators=(",", ":"))
MAX_UPLOAD = 100 * 1024 * 1024  # 通用文件上传上限 100MB (皮肤/音频有自己的更小限制)
MAX_JSON = 1024 * 1024          # 普通 JSON 接口请求体上限 1MB (文件管理/改密码/推送配置等)
SOCK_TIMEOUT = 30               # 单连接读写超时秒数, 防慢连接长期占线程

# ---------------- 版本与在线升级 (OTA) ----------------
# 升级方式: 从 GitHub 仓库拉 version.json (清单) 比对版本号, 再下 sentry-viewer.py 覆盖本机文件并重启。
# 国内直连 raw.githubusercontent 与 fastly/testingcf.jsdelivr 会被 RST 或 TLS 干扰 (实测 1 秒内断开),
# 下面这几个是逐个实测能通的 (数字为实测耗时); 全部并发竞速, 任一可用即可, 抖动时互相补位。
APP_VERSION = "2.8.16"
OTA_REPO = os.environ.get("TSF_OTA_REPO", "baicyber/tesla-sentry-f50")
_OTA_DEFAULT = ",".join([
    "https://ghfast.top/https://raw.githubusercontent.com/%s/main" % OTA_REPO,       # 1.1s
    "https://gcore.jsdelivr.net/gh/%s@main" % OTA_REPO,                              # 1.1s
    "https://ghproxy.net/https://raw.githubusercontent.com/%s/main" % OTA_REPO,      # 2.4s
    "https://gh-proxy.org/https://raw.githubusercontent.com/%s/main" % OTA_REPO,     # 3.2s
    "https://gh-proxy.com/https://raw.githubusercontent.com/%s/main" % OTA_REPO,     # 时通时断, 兜底
    "https://raw.githubusercontent.com/%s/main" % OTA_REPO,                          # 海外线路
    "https://cdn.jsdelivr.net/gh/%s@main" % OTA_REPO,                                # 海外线路
])
OTA_SOURCES = [s.strip().rstrip("/") for s in os.environ.get("TSF_OTA_SOURCES", _OTA_DEFAULT).split(",") if s.strip()]
OTA_BACKUP = os.environ.get("TSF_OTA_BACKUP", "/opt/tesla-sentry/sentry-viewer.bak.py")
OTA_STATE = os.environ.get("TSF_OTA_STATE", "/opt/tesla-sentry/ota-state.json")
OTA_SRC_PREF = os.path.join(os.path.dirname(OTA_STATE) or ".", "ota-src.json")  # 记住上次最快的源
_OTA_CACHE = {"t": 0.0, "data": None}


def _http_get(url, timeout=10, limit=8 << 20):
    req = urllib.request.Request(url, headers={"User-Agent": "tsf-ota/" + APP_VERSION, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)


def _src_order():
    """上次成功的源排最前 (竞速同时成功时优先选它)"""
    order = list(OTA_SOURCES)
    try:
        with open(OTA_SRC_PREF, encoding="utf-8") as f:
            good = json.load(f).get("last_good")
        if good in order:
            order.remove(good)
            order.insert(0, good)
    except Exception:
        pass
    return order


def _err_kind(ex):
    """把网络异常翻成一句人话, 更新页里直接能看出是哪个源被墙/超时/被劫持"""
    s = str(ex)
    if isinstance(ex, urllib.error.HTTPError):
        return "HTTP %d" % ex.code
    if isinstance(ex, socket.timeout) or "timed out" in s:
        return "超时"
    if "RemoteDisconnected" in s or "Connection reset" in s or "reset by peer" in s or "EOF occurred" in s:
        return "连接被阻断"
    if "CERTIFICATE_VERIFY_FAILED" in s or "certificate verify failed" in s:
        return "证书被劫持"
    if "Name does not resolve" in s or "Name or service not known" in s or "getaddrinfo" in s:
        return "DNS 解析失败"
    if "BAD_RECORD_MAC" in s or "WRONG_VERSION_NUMBER" in s or "SSL" in s or "TLS" in s:
        return "TLS 被干扰"
    if "IncompleteRead" in s or "incomplete" in s.lower():
        return "下载中断"
    return type(ex).__name__


def _race_get(path, timeout, limit, validate=None, collect=False, grace=1.0):
    """并发竞速所有下载源, 单个源被墙/超时不影响其它源。
    collect=False: 谁先返回【通过校验】的数据就用谁, 返回 (data, base)。
    collect=True:  返回 [(base, data), ...] 全部成功的源 —— 首次成功后最多再等 grace 秒
                   就收工, 由调用方按版本号挑最新的 (镜像 CDN 常缓存旧清单)。
    全挂时抛错, 并把每个源的失败原因列出来。"""
    lock = threading.Lock()
    got, bad, done = [], {}, [0]
    first_ok = [0.0]

    def worker(base):
        try:
            data = _http_get(base + path, timeout=timeout, limit=limit)
            if data and (validate is None or validate(data)):
                with lock:
                    got.append((base, data))
                    if not first_ok[0]:
                        first_ok[0] = time.time()
            else:
                with lock:
                    bad[base] = "内容校验失败"
        except Exception as ex:
            with lock:
                bad[base] = _err_kind(ex)
        finally:
            with lock:
                done[0] += 1

    order = _src_order()
    for base in order:
        threading.Thread(target=worker, args=(base,), daemon=True).start()
    deadline = time.time() + timeout + 1.5
    while time.time() < deadline:
        with lock:
            n_ok, n_done, t0 = len(got), done[0], first_ok[0]
        if n_done >= len(order):
            break
        if not collect and n_ok:
            break
        if collect and n_ok and t0 and time.time() > t0 + grace:
            break
        time.sleep(0.05)
    with lock:
        results, fails = list(got), dict(bad)
    if not results:
        detail = " · ".join("%s %s" % (urllib.parse.urlsplit(b).netloc, fails.get(b, "无响应"))
                            for b in order)
        raise RuntimeError("所有下载源均不可用 (%s)" % detail)
    results.sort(key=lambda x: order.index(x[0]))
    base = results[0][0]
    try:   # 记住本次最快的源
        with open(OTA_SRC_PREF, "w", encoding="utf-8") as f:
            json.dump({"last_good": base, "at": int(time.time())}, f)
    except OSError:
        pass
    return results if collect else (results[0][1], base)


def _vtuple(v):
    out = []
    for x in re.split(r"[.\-+]", str(v)):
        out.append(int(x) if x.isdigit() else 0)
    return tuple(out) or (0,)


def _api_manifest(timeout=5):
    """GitHub API 实时读 version.json (返回 base64 内容), 不受 raw/镜像 CDN 缓存影响。
    国内不可达时静默失败, 由调用方退回原有的多源竞速结果。"""
    try:
        url = "https://api.github.com/repos/%s/contents/version.json?ref=main" % OTA_REPO
        req = urllib.request.Request(url, headers={
            "User-Agent": "tsf-ota/" + APP_VERSION,
            "Accept": "application/vnd.github+json",
            "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            outer = json.loads(r.read(256 << 10).decode("utf-8", "replace"))
        return json.loads(base64.b64decode(outer["content"]).decode("utf-8", "replace"))
    except Exception:
        return None


def ota_latest(force=False):
    """读仓库 version.json (含版本号/日期/更新说明), 并发竞速多个源, 结果缓存 10 分钟"""
    now = time.time()
    if not force and _OTA_CACHE["data"] and now - _OTA_CACHE["t"] < 600:
        return _OTA_CACHE["data"]

    def ok(raw):
        try:
            j = json.loads(raw.decode("utf-8", "replace"))
            return bool(str(j.get("version") or "").strip())
        except Exception:
            return False

    # raw / ghfast / jsDelivr 的 CDN 会缓存旧清单数分钟, 导致「更新」页显示过期的
    # 版本号与更新说明。并行再问一次 GitHub API (实时, 无 CDN 缓存), 拿到就优先用。
    fresh = []

    def api_worker():
        j = _api_manifest()
        if j and str(j.get("version") or "").strip():
            fresh.append(j)

    apith = threading.Thread(target=api_worker, daemon=True)
    apith.start()

    raced = None
    base = None
    err = ""
    try:
        res = _race_get("/version.json?ts=%d" % int(now), timeout=6, limit=256 << 10,
                        validate=ok, collect=True, grace=1.5)
    except RuntimeError as ex:
        res, err = [], str(ex)
    parsed = []
    for b, r in res:
        try:
            jj = json.loads(r.decode("utf-8", "replace"))
        except Exception:
            continue
        if str(jj.get("version") or "").strip():
            parsed.append((b, jj))
    if parsed:
        # 镜像 CDN 会缓存旧清单 (实测 ghfast.top 拿到过 2.8.10 而仓库已是 2.8.13),
        # 所以不取"第一个成功的", 而是取版本号最大的那个; 同版本时 _race_get 已按源优先级排好
        best = 0
        for i in range(1, len(parsed)):
            if _vtuple(parsed[i][1].get("version")) > _vtuple(parsed[best][1].get("version")):
                best = i
        base, raced = parsed[best]
    else:
        # 一个源都没给出可用清单: 走 GitHub API 兜底, 再不行就把各源的失败原因报上去
        j2 = _api_manifest(timeout=6)
        if j2 and str(j2.get("version") or "").strip():
            raced, base = j2, "https://api.github.com"
        else:
            raise RuntimeError(err or "下载源返回的清单无法解析")
    # jsDelivr 等镜像的 @main 缓存可能停留在很旧的版本: 比本机还旧就多等一会儿实时源
    stale = _vtuple(raced.get("version")) < _vtuple(APP_VERSION)
    if not fresh and apith.is_alive():
        apith.join(timeout=2.5 if stale else 1.2)
    if not fresh and stale:
        j2 = _api_manifest(timeout=3)
        if j2 and str(j2.get("version") or "").strip():
            fresh.append(j2)
    if fresh:
        j, base = fresh[0], "https://api.github.com"
    else:
        j = raced
    j["_base"] = base
    _OTA_CACHE.update({"t": now, "data": j})
    return j


def ota_check(force=False):
    cur = APP_VERSION
    out = {"ok": True, "current": cur, "latest": "", "date": "", "notes": [],
           "has_update": False, "source": "", "error": "",
           "rollback_ready": os.path.isfile(OTA_BACKUP),
           "auto": load_settings().get("auto_update", True),
           "auto_checked": AUTO_UPD["last_check"], "auto_msg": AUTO_UPD["last_result"]}
    try:
        j = ota_latest(force)
    except Exception as ex:
        out["error"] = str(ex)
        return out
    latest = str(j.get("version"))
    out.update({"latest": latest, "date": j.get("date") or "", "notes": j.get("notes") or [],
                "has_update": _vtuple(latest) > _vtuple(cur),
                "source": urllib.parse.urlsplit(j["_base"]).netloc})
    return out


def _self_paths():
    """需要一起覆盖的程序文件 (只认正式安装位置; 临时副本只改它自己)"""
    here = os.path.realpath(__file__)
    known = ("/opt/tesla-sentry/sentry-viewer.py", "/root/sentry-viewer.py")
    paths = [here]
    if here not in known:
        return paths
    for p in known:
        try:
            rp = os.path.realpath(p)
        except OSError:
            continue
        if os.path.isfile(rp) and rp not in paths:
            paths.append(rp)
    return paths


def _write_each(paths, data):
    for p in paths:
        d = os.path.dirname(p) or "."
        tmp = os.path.join(d, ".ota-tmp.py")
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            os.chmod(tmp, 0o644)
        except OSError:
            pass
        os.replace(tmp, p)          # 原子替换, 不会出现半个文件


# OTA 配套脚本: (仓库相对路径, 设备文件名, 特征串, 最小字节数)
# 旧版 OTA 只替换 sentry-viewer.py, 导致改了归档/同步脚本后老设备点"更新"永远不生效。
OTA_SIDECARS = (
    ("scripts/tesla-gadget.sh", "tesla-gadget.sh", b"#!/bin/sh", 200),
    ("scripts/tesla-sync.sh", "tesla-sync.sh", b"#!/bin/sh", 200),
    ("scripts/usb-selfheal.sh", "usb-selfheal.sh", b"#!/bin/sh", 200),
    ("scripts/tesla-img.sh", "tesla-img.sh", b"#!/bin/sh", 200),
    ("scripts/tsf-check.sh", "tsf-check.sh", b"#!/bin/sh", 200),
    ("tesla-live-sync.py", "tesla-live-sync.py", b"def do_sync", 2000),
)


def _ota_sidecars(base):
    """主程序升级后顺带更新配套脚本; 返回 (已更新列表, 跳过/失败数)。
    只覆盖设备上已存在的同名文件; 单个失败不阻断主升级。"""
    d = os.environ.get("TSF_DIR", "/opt/tesla-sentry")
    if not os.path.isdir(d):
        return [], len(OTA_SIDECARS)
    updated, failed = [], 0
    for rel, name, sig, minsz in OTA_SIDECARS:
        dest = os.path.join(d, name)
        if not os.path.isfile(dest):
            failed += 1
            continue
        try:
            data = _http_get("%s/%s?ts=%d" % (base, rel, int(time.time())),
                             timeout=8, limit=512 << 10)
            if len(data) < minsz or sig not in data:
                raise ValueError("内容校验未通过")
            with open(dest, "rb") as f:
                if f.read() == data:
                    continue                      # 内容一致, 不重写也不重启
            tmp = dest + ".ota-tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            try:
                os.chmod(tmp, 0o755 if name.endswith(".sh") else 0o644)
            except OSError:
                pass
            os.replace(tmp, dest)
            updated.append(name)
        except Exception as ex:
            failed += 1
            try:
                os.unlink(dest + ".ota-tmp")
            except OSError:
                pass
            print("ota sidecar skip %s: %s" % (name, ex))
    # 在线同步脚本换了必须重启常驻进程, 否则跑的还是旧代码
    if "tesla-live-sync.py" in updated:
        try:
            if os.path.isfile("/etc/init.d/tesla-live"):
                subprocess.Popen(["/etc/init.d/tesla-live", "restart"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif shutil.which("systemctl"):
                subprocess.Popen(["systemctl", "restart", "tesla-live.service"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    return updated, failed


def _ota_restart():
    """优先交给 procd 的 init 脚本重启 (找不到就原地 exec)"""
    here = os.path.realpath(__file__)
    d = os.path.dirname(here)
    cands = []
    for name in ("tesla-viewer", "sentry-viewer"):
        p = "/etc/init.d/" + name
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            txt = ""
        score = 2 if here in txt else (1 if ("sentry-viewer.py" in txt or (d and d in txt)) else 0)
        cands.append((score, p))
    cands.sort(reverse=True)
    if cands and cands[0][0] > 0:
        try:
            subprocess.Popen([cands[0][1], "restart"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
        except Exception:
            pass
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        os._exit(1)


def ota_apply():
    """下载并安装最新版; 返回 (成功?, 提示/错误)"""
    try:
        j = ota_latest(True)
    except Exception as ex:
        return False, "检查更新失败: %s" % ex
    latest = str(j.get("version"))
    if not _vtuple(latest) > _vtuple(APP_VERSION):
        return False, "当前已是最新版本 (%s)" % APP_VERSION
    # 下载也走多源竞速: 清单来源的镜像突然挂了, 其它源照样能补位
    try:
        data, base = _race_get("/sentry-viewer.py?ts=%d" % int(time.time()), timeout=60, limit=8 << 20,
                               validate=lambda d: len(d) > 50000 and b"APP_VERSION" in d)
    except Exception as ex:
        return False, "下载失败: %s" % ex
    # 1) 完整性校验: 大小 + 特征串 + 内嵌版本号必须与清单一致
    if len(data) < 50000:
        return False, "下载不完整 (%d 字节)" % len(data)
    if b"APP_VERSION" not in data or b"--ota-check" not in data or b"/api/events" not in data:
        return False, "文件校验失败: 不是有效的查看器程序"
    m = re.search(rb'APP_VERSION\s*=\s*"([^"]+)"', data)
    if not m or m.group(1).decode() != latest:
        return False, "版本校验失败 (文件 %s / 清单 %s)" % (m.group(1).decode() if m else "?", latest)
    # 2) 语法 + 启动自检: 新程序自己声明的 --ota-check 只做模块加载, 不启服务
    tmp = "/tmp/tsf-ota-new.py"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        r = subprocess.run([sys.executable or "python3", "-m", "py_compile", tmp],
                           capture_output=True, timeout=90)
        if r.returncode != 0:
            return False, "语法校验失败: " + r.stderr.decode("utf-8", "replace")[-160:]
        r = subprocess.run([sys.executable or "python3", tmp, "--ota-check"],
                           capture_output=True, timeout=90)
        if r.returncode != 0 or b"ok" not in (r.stdout or b"").lower():
            return False, "启动自检失败: " + (r.stderr or r.stdout).decode("utf-8", "replace")[-160:]
        os.remove(tmp)
    except Exception as ex:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, "校验异常: %s" % ex
    # 3) 备份旧版 (回滚用) 后逐份原子替换
    here = os.path.realpath(__file__)
    try:
        os.makedirs(os.path.dirname(OTA_BACKUP) or ".", exist_ok=True)
        shutil.copy2(here, OTA_BACKUP)
    except Exception as ex:
        return False, "备份旧版本失败: %s" % ex
    prev = APP_VERSION
    try:
        _write_each(_self_paths(), data)
    except Exception as ex:
        return False, "写入失败: %s" % ex
    # 3.5) 配套脚本一并升级 (单个失败不影响主程序; 内容一致则跳过不重写)
    try:
        upd, skipped = _ota_sidecars(base)
    except Exception as ex:
        upd, skipped = [], len(OTA_SIDECARS)
        print("ota sidecars error:", ex)
    try:
        with open(OTA_STATE, "w", encoding="utf-8") as f:
            json.dump({"applied": latest, "prev": prev, "at": int(time.time())}, f)
    except OSError:
        pass
    msg = "已升级到 %s, 服务正在重启…" % latest
    if upd:
        msg += " 配套脚本已更新: " + ", ".join(upd)
    if skipped:
        msg += " (%d 个脚本源不可用, 保持原文件)" % skipped
    threading.Timer(1.2, _ota_restart).start()
    return True, msg


def ota_rollback():
    if not os.path.isfile(OTA_BACKUP):
        return False, "没有可用的备份 (还没升级过)"
    try:
        with open(OTA_BACKUP, "rb") as f:
            data = f.read()
    except OSError as ex:
        return False, "读取备份失败: %s" % ex
    if len(data) < 50000:
        return False, "备份文件不完整, 已放弃回滚"
    try:
        _write_each(_self_paths(), data)
    except Exception as ex:
        return False, "写入失败: %s" % ex
    threading.Timer(1.0, _ota_restart).start()
    return True, "已回滚到上一版本, 服务正在重启…"


# ---------- 自动更新 ----------
# 设置存在 settings.json (与 auth.conf 同目录), 默认开启: 每 5 分钟看一次有没有新版本,
# 有就等"没人在看视频/没有导出任务"的窗口自动升级 (升级只重启本服务, 约 5 秒)
SETTINGS_FILE = os.environ.get("TSF_SETTINGS", "/opt/tesla-sentry/settings.json")
AUTO_UPD = {"last_check": 0, "last_result": "", "last_apply": 0, "next_try": 0}


def load_settings():
    d = {"auto_update": True}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict) and "auto_update" in j:
            d["auto_update"] = bool(j["auto_update"])
    except (OSError, ValueError):
        pass
    return d


def save_settings(patch):
    d = load_settings()
    d.update(patch or {})
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE) or ".", exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        pass
    return d


def _update_window_ok():
    """自动升级前探一下: 有人在拉视频/正在导出时先不动, 免得把人正在看的东西打断"""
    try:
        if MEDIA_ACTIVE[0] > 0:
            return False
    except Exception:
        pass
    with STITCH_LOCK:
        if any(j.get("state") == "running" for j in STITCH_JOBS.values()):
            return False
    return True


def auto_update_loop():
    time.sleep(90)                       # 开机先让同步与服务稳定
    while True:
        try:
            if not load_settings().get("auto_update", True):
                time.sleep(300)
                continue
            if time.time() < AUTO_UPD["next_try"]:
                time.sleep(60)
                continue
            j = ota_check(False)
            AUTO_UPD["last_check"] = int(time.time())
            if j.get("error"):
                AUTO_UPD["last_result"] = "检查失败: " + j["error"]
                AUTO_UPD["next_try"] = time.time() + 1800
                time.sleep(60)
                continue
            if not j.get("has_update"):
                AUTO_UPD["last_result"] = "已是最新 (%s)" % APP_VERSION
                AUTO_UPD["next_try"] = time.time() + 1800
                time.sleep(60)
                continue
            AUTO_UPD["last_result"] = "发现 v%s, 等待空闲后自动升级" % j.get("latest")
            for _ in range(30):          # 最多等 30 分钟的空闲窗口
                if _update_window_ok():
                    break
                time.sleep(60)
            if not _update_window_ok():
                AUTO_UPD["next_try"] = time.time() + 1800
                time.sleep(60)
                continue
            ok, msg = ota_apply()
            AUTO_UPD["last_apply"] = int(time.time())
            AUTO_UPD["last_result"] = ("自动升级: " if ok else "自动升级失败: ") + str(msg)
            if ok:
                time.sleep(30)           # ota_apply 已安排 1.2s 后重启, 进程会被替换
            AUTO_UPD["next_try"] = time.time() + 1800
            time.sleep(60)
        except Exception as ex:
            AUTO_UPD["last_result"] = "自动更新异常: %s" % ex
            AUTO_UPD["next_try"] = time.time() + 1800
            time.sleep(120)









# 赞助 / 联系作者二维码 (压缩后 base64 内嵌, 单文件自包含, OTA 随主程序一起更新)
# 图片走 /sponsor/<key> 按需加载, 不塞进首页 HTML, 减少窄带下每次刷新的传输量
SPONSOR_IMGS = {
    "alipay": "data:image/jpeg;base64,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",
    "wechatpay": "data:image/jpeg;base64,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",
    "douyin": "data:image/jpeg;base64,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",
    "wechatauthor": "data:image/jpeg;base64,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",
}
SPONSOR_BYTES = {k: base64.b64decode(v.split(",", 1)[1]) for k, v in SPONSOR_IMGS.items()}


HTML = r"""<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>特斯拉哨兵查看器</title>
<style id="base">
:root {
  color-scheme: dark;
  --bg:#0e1216; --headbg:#171d24; --panel:#1c232c; --panel2:#232b35; --chipbg:#1e252e;
  --line:#232b34; --line2:#2a333d; --text:#e8eaed; --muted:#98a4b0; --muted2:#8b96a3;
  --accent:#e8443f; --blue:#2f6ee0; --shadow:0 4px 14px #00000055; --headline:#ffffff0d;
  /* 播放器子页面专用 */
  --stage:#0d1117;                        /* 视频区域始终深色, 两种主题都不变 */
  --amberbg:rgba(245,158,11,.14); --amber:#eab063; --amberb:#f7c67e; --amberline:rgba(245,158,11,.22);
}
html[data-theme="light"] {
  color-scheme: light;
  --bg:#f2f4f7; --headbg:#ffffff; --panel:#ffffff; --panel2:#eef1f5; --chipbg:#ffffff;
  --line:#e4e8ee; --line2:#d9dfe6; --text:#17191c; --muted:#5c6773; --muted2:#78838f;
  --accent:#e8443f; --blue:#2f6ee0; --shadow:0 2px 10px #0f172a14; --headline:#0f172a0d;
  --amberbg:rgba(245,158,11,.16); --amber:#9a5b00; --amberb:#875000; --amberline:rgba(245,158,11,.34);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  transition:background .25s,color .25s; }
header { padding:12px 16px; background:var(--headbg); display:flex; flex-wrap:wrap; align-items:center; gap:10px; position:sticky; top:0; z-index:10; box-shadow:0 1px 0 var(--headline); }
header h1 { font-size:16px; font-weight:700; white-space:nowrap; letter-spacing:.5px; flex:0 0 auto; }
header .sub { font-size:12px; color:var(--muted); white-space:nowrap; }
#live { margin-left:auto; min-width:0; flex:0 1 auto; }
#livedot { flex:0 0 7px; }
#livetxt { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tabs { display:flex; gap:6px; overflow-x:auto; scrollbar-width:none; }
.tabs::-webkit-scrollbar { display:none; }
.tab { padding:7px 14px; border-radius:18px; background:var(--panel2); color:var(--muted); cursor:pointer; font-size:12.5px; font-weight:600; border:1px solid transparent; white-space:nowrap; flex:0 0 auto; transition:background .15s,color .15s; }
.tab .tn { margin-left:5px; opacity:.62; font-variant-numeric:tabular-nums; }
.tab .tn::before { content:'('; } .tab .tn::after { content:')'; }
.tab:hover { color:var(--text); }
.tab.active { background:var(--text); color:var(--bg); box-shadow:none; }
html[data-theme="light"] .tab.active { background:#17191c; color:#fff; }
.filters { display:none; gap:6px; overflow-x:auto; padding:10px 16px 0; scrollbar-width:none; align-items:center; }
.filters.show { display:flex; }
.filters + .filters { padding-top:8px; }
.filters::-webkit-scrollbar { display:none; }
.flab { font-size:11.5px; color:var(--muted2); flex:0 0 auto; letter-spacing:.5px; }
.chip.d.on { background:var(--blue); border-color:var(--blue); }
.chip.pick { border-style:dashed; background:var(--chipbg); color:var(--muted);
  position:sticky; right:0; z-index:2; box-shadow:-10px 0 12px var(--bg); }
.chip.pick:hover { color:var(--text); }
/* 原生日期选择器本体: 藏起来只用来唤起系统日历 */
.dpick { position:absolute; left:0; top:0; width:1px; height:1px; opacity:0; border:0; padding:0;
  pointer-events:none; }
.chip { padding:8px 13px; border-radius:14px; background:var(--chipbg); color:var(--muted); border:1px solid var(--line2);
  font-size:12px; cursor:pointer; white-space:nowrap; flex:0 0 auto; transition:background .15s,color .15s; }
.chip:hover { color:var(--text); border-color:var(--muted2); }
.chip.on { background:var(--blue); border-color:var(--blue); color:#fff; }
.chip .n { opacity:.62; margin-left:4px; font-variant-numeric:tabular-nums; }
/* 存储卡: 默认只露「虚拟U盘」一行, 右下角小箭头展开归档区/总占用
   (行距刻意压紧, 收起时约 40px, 手机首屏别占太多) */
.storwrap { padding:8px 16px 0; }
.storcard { position:relative; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:8px 13px 9px; box-shadow:var(--shadow); }
.storrow { display:flex; justify-content:space-between; align-items:baseline; gap:10px; margin-bottom:4px; }
.storrow + .storbar { margin-bottom:7px; }
.storbar:last-child { margin-bottom:0; }
.storlab { font-size:12px; color:var(--text); font-weight:600; }
.storval { font-size:11.5px; color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
/* 进度条右侧留出小箭头的位子, 三行等宽 */
.storbar { height:4px; margin-right:24px; border-radius:2px; background:var(--line2); overflow:hidden; }
.storbar i { display:block; height:100%; width:0; background:var(--blue); border-radius:2px; transition:width .4s; }
.storextra { display:none; }
.storcard.open .storextra { display:block; }
.stortog { position:absolute; right:4px; bottom:3px; width:22px; height:20px; padding:0; border:0; border-radius:6px;
  background:transparent; color:var(--muted2); cursor:pointer; display:flex; align-items:center; justify-content:center;
  -webkit-tap-highlight-color:transparent; }
.stortog svg { width:13px; height:13px; transition:transform .2s; }
.stortog:hover { color:var(--text); background:var(--panel2); }
.storcard.open .stortog svg { transform:rotate(180deg); }
/* 主题切换按钮: 自动/浅色/深色 循环 */
#theme { background:var(--panel2); border:none; color:var(--muted); font-size:15px; cursor:pointer; border-radius:10px; width:36px; height:32px; }
#theme:hover { color:var(--text); }
@media (max-width:640px){
  /* 顶栏压成两行: 第一行 标题+同步状态+主题/设置, 第二行 4 个等宽页签 */
  header { padding:9px 12px 8px; gap:8px; }
  header h1 { font-size:15px; }
  header .sub { display:none; }
  #live { order:3; margin-left:0; max-width:108px; padding:4px 8px; }
  #theme { order:4; margin-left:auto; } #gear { order:5; margin-left:0; } #out { order:6; margin-left:0; }
  /* 页签: 单行「分段控件」—— 灰槽里浮起一块选中的, 比两行卡片矮一半;
     计数收成右侧小徽章, 「实时预览」没有计数也不会跟旁边几个对不齐 */
  .tabs { order:10; width:100%; margin-left:0; gap:2px; overflow:visible;
    background:var(--panel2); border:1px solid var(--line); border-radius:13px; padding:3px; }
  .tab { flex:1 1 0; min-width:0; display:flex; flex-direction:row; align-items:center;
    justify-content:center; gap:4px; padding:7px 2px; font-size:12px; line-height:1.2;
    border-radius:10px; background:transparent; border:none; overflow:hidden; }
  .tab .tl { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .tab .tn { flex:0 0 auto; display:inline-block; margin-left:0; padding:0 5px;
    font-size:10.5px; line-height:1.4; font-weight:700; border-radius:7px;
    background:#ffffff14; color:var(--muted); opacity:1; }
  html[data-theme="light"] .tab .tn { background:#0f172a10; }
  .tab .tn::before, .tab .tn::after { content:none; }
  .tab.active { box-shadow:0 1px 4px #0000004d; }
  html[data-theme="light"] .tab.active { box-shadow:0 1px 4px #0f172a29; }
  .tab.active .tn { background:#ffffff26; color:inherit; }
  .filters { padding-left:12px; padding-right:12px; }
  .chip { padding:8px 12px; }
  /* 播放器手机端紧凑化: 面板/卡片占高更小, 视频尽量多露 */
  .pscroll { padding:2px 8px 10px; gap:8px; }
  .tpanel { padding:10px 12px 8px; }
  .tp-spd { font-size:26px; }
  .tp-date { font-size:12px; } .tp-loc { font-size:11.5px; }
  .tp-tag { height:20px; font-size:11px; padding:0 8px; }
  .tp-gear { width:22px; height:22px; font-size:12px; }
  .tp-sig svg { width:20px; height:20px; }
  .tp-c { width:28px; height:28px; } .tp-c.st svg { width:16px; height:16px; }
  .tp-badge { height:25px; font-size:11px; padding:0 8px 0 6px; } .tp-badge svg { width:13px; height:13px; }
  .infocard { padding:10px 12px 8px; }
  .ptop { padding:6px 10px; }
  /* 底部控制栏: 手机宽度紧张, 全面紧凑化 */
  .pbar { gap:8px; padding-left:10px; padding-right:10px; }
  .pp { width:38px; height:38px; font-size:14px; }
  .pbar .t { min-width:38px; font-size:12.5px; }
  .spd { padding:8px 11px; font-size:13px; }
  .qbtn { padding:8px 10px; font-size:12.5px; }
}
#gear { background:var(--panel2); border:none; color:var(--muted); font-size:18px; cursor:pointer; border-radius:10px; width:36px; height:32px; }
#gear:hover { color:var(--text); }
#out { background:var(--panel2); border:none; color:var(--muted); font-size:15px; cursor:pointer; border-radius:10px; width:36px; height:32px; }
#out:hover { color:var(--text); }
.grid-wrap { padding:14px 16px 20px; display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:10px; max-width:1180px; margin:0 auto; }
/* 事件卡片: 官方App横排列表 (封面左, 信息右) */
.card { display:flex; gap:12px; align-items:center; background:var(--panel); border-radius:14px; overflow:hidden; cursor:pointer; transition:transform .15s,box-shadow .15s; border:1px solid var(--line); box-shadow:var(--shadow); padding:9px; }
.card:hover { transform:translateY(-2px); }
.thumb { flex:0 0 132px; width:132px; height:76px; border-radius:10px; overflow:hidden; background:#000; position:relative; }
.thumb img { width:100%; height:100%; object-fit:cover; display:block; }
.cnum { position:absolute; right:5px; bottom:5px; background:#000000b8; color:#fff; font-size:10.5px; line-height:1; padding:3px 6px; border-radius:8px; }
.thumb .ph { background:var(--panel2); height:100%; display:flex; align-items:center; justify-content:center; color:var(--muted2); font-size:20px; }
.meta { flex:1; min-width:0; }
.meta .l1 { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
.meta .rt { font-size:13.5px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.meta .tm { font-size:12px; color:var(--muted); white-space:nowrap; flex:0 0 auto; font-variant-numeric:tabular-nums; }
.meta .l2 { display:flex; justify-content:space-between; gap:8px; margin-top:4px; font-size:11.5px; color:var(--muted); white-space:nowrap; overflow:hidden; }
.meta .l2 span { overflow:hidden; text-overflow:ellipsis; }
.meta .l2 .sz { flex:0 0 auto; font-variant-numeric:tabular-nums; }
/* 日期分组小标题 (全部日期视图) */
.dhead { grid-column:1/-1; display:flex; align-items:baseline; gap:8px; padding:8px 2px 0; }
.dhead b { font-size:13px; font-weight:700; }
.dhead .n { font-size:11.5px; color:var(--muted2); }
.player { display:none; position:fixed; inset:0; height:100vh; height:100dvh; background:var(--bg); color:var(--text); z-index:100; flex-direction:column; }
/* 播放器子页面跟随全局主题: 卡片/头部/控制栏用变量, 只有视频画面区域保持恒定深色 (--stage), 看片更沉浸 */
.player.open { display:flex; }
.ptop { display:flex; align-items:center; gap:10px; padding:10px 14px; }
.ptop .pdl { margin-left:auto; }
.ptitle { flex:1; min-width:0; text-align:center; font-size:12.5px; font-weight:600; color:var(--muted);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-variant-numeric:tabular-nums; }
.pback { background:none; border:none; color:var(--text); font-size:16.5px; cursor:pointer; padding:6px 4px; white-space:nowrap; }
.pdl { background:none; border:none; color:var(--text); font-size:19px; cursor:pointer; padding:6px 8px; }
.pscroll { flex:1; min-height:0; overflow-y:auto; -webkit-overflow-scrolling:touch; padding:2px 14px 14px; display:flex; flex-direction:column; gap:12px; }
.pscroll > * { flex:0 0 auto; }
.vwrap { position:relative; border-radius:14px; overflow:hidden; max-width:980px; width:100%; margin:0 auto; background:var(--stage); }
/* 行车遥测面板 (视频上方, Tesla App 样式, 不遮挡画面) */
.tpanel { display:none; background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px 18px 10px; max-width:980px; width:100%; margin:0 auto; }
.tpanel.show { display:block; }
.tpanel.folded .tp-rows { display:none; }
/* 无遥测事件(哨兵/停车): 顶部只保留 时间 + 城市·位置 一行, 不留空白块 */
.tpanel.plain { padding:12px 18px; }
.tpanel.plain .tp-mid,
.tpanel.plain .tp-fold,
.tpanel.plain .tp-rows,
.tpanel.plain .tp-tr { display:none; }
.infocard.plain .ic-time { display:none; }
.infocard.plain .ic-badge { margin-left:0; }
.tp-top { display:flex; justify-content:space-between; align-items:center; }
.tp-l { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
.tp-date { font-size:13.5px; color:var(--text); font-variant-numeric:tabular-nums; }
.tp-loc { font-size:12.5px; color:var(--muted); }
/* 哨兵/纪念事件: 触发画面摄像头 (来自 event.json 的 camera 字段) */
.tp-tag { display:none; align-items:center; gap:4px; height:22px; padding:0 9px; border-radius:11px;
  background:var(--amberbg); color:var(--amber); font-size:11.5px; font-weight:600;
  border:1px solid var(--amberline); white-space:nowrap; align-self:center; }
.tp-tag.show { display:inline-flex; }
.tp-tag b { color:var(--amberb); font-weight:800; font-variant-numeric:tabular-nums; }
.tp-tr { display:flex; align-items:center; gap:6px; }
/* 转向灯: 矢量箭头(圆角方块底 + 白色箭头), 不用 ⬅➡ emoji —— emoji 是彩色字形,
   不认 CSS color, 点亮时颜色不会变, 看起来永远像"静止的图标" */
.tp-sig { display:inline-flex; align-items:center; color:var(--muted2); line-height:1; opacity:.5; }
.tp-sig svg { width:23px; height:23px; display:block; }
.tp-sig.on { color:#f59e0b; opacity:1; animation:tpBlink 1.15s steps(1,end) infinite; }
@keyframes tpBlink { 0%, 52% { opacity:1 } 53%, 100% { opacity:.18 } }
@media (prefers-reduced-motion: reduce) { .tp-sig.on { animation:none } }
.tp-gear { background:#34c759; color:#fff; font-weight:800; font-size:14px; width:26px; height:26px;
  border-radius:50%; display:inline-flex; align-items:center; justify-content:center; }
.tp-mid { display:flex; align-items:center; margin-top:8px; }
.tp-spd { font-size:34px; font-weight:800; color:var(--text); line-height:1.1; font-variant-numeric:tabular-nums; }
.tp-unit { font-size:13px; color:var(--muted); font-weight:600; margin-left:6px; }
.tp-badges { display:flex; align-items:center; gap:10px; margin-left:auto; }
.tp-c { width:34px; height:34px; border-radius:50%; display:inline-flex; align-items:center;
  justify-content:center; font-size:11.5px; font-weight:700; flex:none; }
.tp-c.t { background:var(--panel2); color:var(--text); font-size:15px; }
.tp-badge { display:inline-flex; align-items:center; gap:5px; height:30px; padding:0 11px 0 8px;
  border-radius:15px; font-size:12.5px; font-weight:700; font-variant-numeric:tabular-nums; flex:none; }
.tp-badge svg { width:16px; height:16px; flex:none; }
.tp-badge.thr { background:#1f7ae0; color:#fff; }
.tp-badge.brk { background:var(--panel2); color:var(--muted); }
.tp-badge.brk.on { background:#ff453a; color:#fff; box-shadow:0 0 9px #ff453a66; }
.tp-c.ap { background:linear-gradient(135deg,#f59e0b,#ef4444); color:#fff; font-size:10.5px;
  box-shadow:0 0 10px #ef444455; }
/* 未启动: 同一个 AP 图标, 只是灰掉 (不是隐藏, 也不带文案), 一眼能看出"有这功能但没开" */
.tp-c.ap.off { background:var(--panel2); color:var(--muted); box-shadow:inset 0 0 0 1px #ffffff2e; }
.tp-c.st { background:var(--panel2); color:var(--muted); }
.tp-c.st svg { width:20px; height:20px; }
.tp-fold { display:block; margin:8px 0 0 auto; background:none; border:none; color:var(--muted);
  font-size:12px; cursor:pointer; padding:4px 2px; }
.tp-rows { border-top:1px solid var(--line); margin-top:2px; padding-top:8px; display:grid;
  grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:1px 20px; }
.tp-rows div { color:var(--text); font-size:13px; padding:3px 0; font-variant-numeric:tabular-nums; }
.tp-rows .k { color:var(--muted); margin-right:9px; }
.vgrid { display:grid; gap:3px; background:var(--stage); }
.vcell { position:relative; overflow:hidden; background:var(--stage); cursor:zoom-in; aspect-ratio:16/9; }
.vcell.focused { grid-column:1/-1; grid-row:1/-1; cursor:zoom-out; }
.vcell video { width:100%; height:100%; background:var(--stage); object-fit:cover; display:block; }
/* 以下是叠在视频画面上的浮层, 永远在深色画面上, 用固定颜色 */
.startmask { position:absolute; inset:0; z-index:5; background:rgba(0,0,0,.55); color:#fff; font-size:16px; display:none; align-items:center; justify-content:center; cursor:pointer; letter-spacing:1px; }
.startmask.show { display:flex; }
.vcell .clabel { position:absolute; right:7px; bottom:7px; font-size:11px; color:#e8eef4; background:rgba(20,24,29,.72); border-radius:6px; padding:2px 7px; pointer-events:none; }
.vcell .cload { position:absolute; left:0; top:0; right:0; bottom:0; display:flex; align-items:center; justify-content:center; gap:7px; font-size:12.5px; color:#c6cdd4; background:rgba(10,13,18,.6); pointer-events:none; }
.vcell .cload.err { color:#ff6b66; }
.vcell .cload .spin { width:14px; height:14px; border:2px solid #3a4450; border-top-color:#10b981; border-radius:50%; animation:cspin .8s linear infinite; }
@keyframes cspin { to { transform:rotate(360deg); } }
.infocard { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:14px 16px 10px; max-width:980px; width:100%; margin:0 auto; box-shadow:var(--shadow); }
.ic-top { display:flex; align-items:center; gap:10px; }
.ic-time { font-size:14px; color:var(--text); }
.ic-badge { margin-left:auto; background:#3b82f6; color:#fff; font-size:12px; font-weight:600; border-radius:14px; padding:3px 12px; }
.ic-mid { display:flex; align-items:baseline; gap:12px; margin:10px 0 2px; flex-wrap:wrap; }
.ic-big { font-size:30px; font-weight:700; letter-spacing:.5px; color:var(--text); }
.ic-sub { font-size:12.5px; color:var(--muted); }
.clipbar { display:none; gap:6px; flex-wrap:wrap; margin-top:9px; }
.clipbar.show { display:flex; }
.clipbar button { background:var(--panel2); color:var(--muted); border:1px solid var(--line2); border-radius:8px;
  font-size:12px; padding:9px 11px; cursor:pointer; font-variant-numeric:tabular-nums; }
.clipbar button.on { background:var(--blue); border-color:var(--blue); color:#fff; }
.ic-rows { display:none; font-size:12.5px; line-height:1.9; color:var(--muted); word-break:break-all; border-top:1px solid var(--line); margin-top:8px; padding-top:8px; }
.ic-rows.open { display:block; }
.ick { display:inline-block; min-width:64px; color:var(--muted); }
.ic-fold { background:none; border:none; color:var(--muted); font-size:12px; cursor:pointer; padding:10px 2px 6px; margin-left:auto; display:block; }
.pbar { display:flex; align-items:center; gap:12px; padding:10px 16px calc(12px + env(safe-area-inset-bottom,0px)); max-width:1008px; margin:0 auto; width:100%; box-sizing:border-box; }
.pp { width:44px; height:44px; border-radius:50%; background:var(--text); color:var(--panel); border:none; font-size:16px; cursor:pointer; flex:0 0 auto; }
.pbar input[type=range] { flex:1; accent-color:#3b82f6; min-width:80px; }
.pbar .t { font-size:13.5px; color:var(--text); min-width:44px; text-align:center; }
.spd { background:#3b82f6; color:#fff; border:none; border-radius:10px; padding:9px 15px; font-size:14px; font-weight:600; cursor:pointer; flex:0 0 auto; }
.qsel { position:relative; flex:0 0 auto; }
.qbtn { background:var(--panel2); color:var(--muted); border:1px solid var(--line2); border-radius:10px; padding:9px 12px; font-size:13px; font-weight:600; cursor:pointer; white-space:nowrap; }
.qbtn.on { background:#10b981; border-color:#10b981; color:#fff; }
.qmenu { display:none; position:absolute; right:0; bottom:calc(100% + 6px); background:var(--panel); border:1px solid var(--line2); border-radius:10px; padding:4px; min-width:96px; box-shadow:0 8px 22px #00000040; z-index:30; }
.qsel.open .qmenu { display:block; }
.qmenu button { display:block; width:100%; text-align:left; background:none; border:none; color:var(--text); font-size:13px; padding:9px 12px; border-radius:7px; cursor:pointer; white-space:nowrap; }
.qmenu button.on { background:#10b981; color:#fff; }
.qhint { font-size:12px; color:var(--amber); flex:0 0 100%; text-align:left; display:none; }
.empty { text-align:center; padding:80px 20px; color:var(--muted2); }
.empty .big { font-size:44px; margin-bottom:14px; }
/* 设置面板 */
.mask { display:none; position:fixed; inset:0; background:rgba(0,0,0,.45); z-index:200; }
.mask.open { display:block; }
.panel { position:fixed; top:50%; left:50%; transform:translate(-50%,-50%); width:min(560px,94vw); max-height:86vh; overflow:auto; background:var(--panel); border-radius:14px; z-index:201; padding:18px; display:none; box-shadow:0 12px 40px #00000040; }
.panel.open { display:block; }
.panel h2 { font-size:15px; margin-bottom:12px; display:flex; align-items:center; }
.panel h2 .x { margin-left:auto; background:none; border:none; color:var(--muted); font-size:20px; cursor:pointer; padding:4px 8px; }
.stabs { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
.stab { flex:0 1 calc(33.333% - 6px); min-width:0; padding:10px 4px; border-radius:10px; background:var(--panel2); color:var(--muted); text-align:center; cursor:pointer; font-size:12.5px; border:none; white-space:nowrap; }
.stab.active { background:var(--accent); color:#fff; }
.langgrid { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:10px; }
.langgrid button { padding:10px 6px; border-radius:10px; background:var(--panel2); border:1px solid var(--line2);
  color:var(--text); font-size:13px; cursor:pointer; text-align:center; line-height:1.35; }
.langgrid button .code { display:block; font-size:10.5px; color:var(--muted2); margin-top:2px; letter-spacing:.5px; }
.langgrid button.on { border-color:var(--accent); box-shadow:0 0 0 2px var(--accent) inset; color:var(--accent); }
.langgrid button.on .code { color:var(--accent); opacity:.8; }
.hint { font-size:11.5px; color:var(--muted2); line-height:1.6; margin-bottom:10px; }
/* 飞书推送 */
.ninput { display:block; width:100%; box-sizing:border-box; margin-bottom:8px; padding:9px 11px; border-radius:9px;
  background:var(--panel2); border:1px solid var(--line2); color:var(--text); font-size:12.5px; outline:none; }
.ninput:focus { border-color:var(--accent); }
.nrow { display:flex; align-items:center; gap:8px; margin-bottom:8px; font-size:13px; color:var(--text); }
.nrow > span:first-child { flex:0 0 auto; }
.nrow input[type=checkbox] { width:18px; height:18px; accent-color:var(--accent); margin-left:auto; }
.ninput.num { width:76px; margin:0; text-align:center; }
.ninput.masked { color:var(--muted2); letter-spacing:1.5px; cursor:default; border-style:dashed; }
select.ninput { cursor:pointer; }
.eyebar { display:flex; align-items:center; gap:8px; margin:0 0 8px; }
.eyebar .lbl { flex:1; font-size:11.5px; color:var(--muted2); }
.eyebar .mini { flex:0 0 auto; padding:6px 12px; border-radius:8px; background:var(--panel2);
  border:1px solid var(--line2); color:var(--text); font-size:12px; cursor:pointer; }
.eyebar .mini:hover { border-color:var(--accent); }
.hint2 { font-size:11.5px; color:var(--muted2); }
/* 系统更新 (OTA) */
.updrow { display:flex; align-items:baseline; gap:8px; padding:7px 0; font-size:13px; }
.updk { color:var(--muted); min-width:64px; flex:0 0 auto; }
.updtag { font-size:11px; padding:1px 8px; border-radius:9px; background:var(--amberbg); color:var(--amber); border:1px solid var(--amberline); white-space:nowrap; }
.updtag.new { background:rgba(232,68,63,.14); color:var(--accent); border-color:rgba(232,68,63,.32); }
.updnotes { display:none; background:var(--panel2); border-radius:10px; padding:9px 12px; font-size:12.5px; line-height:1.85; color:var(--muted); margin:6px 0 2px; }
/* 自动更新开关: 一行 + 右侧勾选框, 默认勾上 */
.updauto { display:flex; align-items:center; gap:8px; margin:10px 0 2px; padding-top:10px;
  border-top:1px solid var(--line); font-size:13px; color:var(--text); cursor:pointer; }
.updauto span { flex:1; }
.updauto input[type=checkbox] { width:18px; height:18px; margin:0; accent-color:var(--accent); cursor:pointer; }
.updbtns { display:flex; gap:8px; margin:10px 0 8px; }
.updbtns .mini { flex:1; padding:9px 0; border-radius:9px; background:var(--panel2); border:none; color:var(--text); font-size:13px; cursor:pointer; }
.updbtns .mini.logout { background:transparent; border:1px solid var(--accent); color:var(--accent); }
.updbtns .mini.primary { background:var(--accent); color:#fff; }
.updbtns .mini:disabled { opacity:.45; cursor:default; }
#gear { position:relative; }
#gear.hasupd::after { content:''; position:absolute; top:4px; right:4px; width:7px; height:7px; border-radius:50%; background:var(--accent); box-shadow:0 0 0 2px var(--headbg); }
.upbtn { display:block; width:100%; padding:10px; border:1px dashed var(--line2); border-radius:10px; color:var(--muted); text-align:center; cursor:pointer; font-size:13px; margin-bottom:12px; background:var(--panel2); }
.upbtn:hover { border-color:var(--accent); color:var(--text); }
.item { display:flex; align-items:center; gap:10px; background:var(--panel2); border-radius:10px; padding:9px 12px; margin-bottom:8px; font-size:13px; }
.item .n { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.item button { background:var(--panel2); border:1px solid var(--line2); color:var(--muted); border-radius:7px; padding:5px 10px; font-size:12px; cursor:pointer; }
.item button.danger:hover { background:var(--accent); border-color:var(--accent); color:#fff; }
.item button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
/* 文件管理器: 多选与批量操作 */
.item .fm-chk { width:17px; height:17px; flex:0 0 auto; accent-color:var(--accent); cursor:pointer; }
.item.fm-sel { box-shadow:inset 0 0 0 1px var(--accent); }
.item .fm-mini { padding:7px 10px; min-height:32px; min-width:32px; }
.fm-bar { background:var(--panel2); border:1px solid var(--line2); border-radius:10px; padding:10px; margin-bottom:10px; }
.fm-bar .row { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
.fm-bar button { background:var(--panel2); border:1px solid var(--line2); color:var(--text); border-radius:8px; padding:7px 10px; font-size:12px; cursor:pointer; }
.fm-bar button:hover { border-color:var(--accent); color:var(--accent); }
.fm-bar button.fm-danger { color:var(--accent); border-color:var(--accent); }
.fm-bar button.fm-go { background:var(--accent); border-color:var(--accent); color:#fff; }
.fm-bar button.fm-go:hover { color:#fff; }
.fm-bar .fm-dest-input { flex:1; min-width:130px; background:var(--bg); border:1px solid var(--line2); border-radius:8px; color:var(--text); padding:7px 9px; font-size:12px; outline:none; }
.fm-count { font-size:12px; color:var(--muted); }
.fm-purge { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:0 0 10px; }
.fm-purge button { background:transparent; border:1px solid var(--accent); color:var(--accent); border-radius:8px; padding:8px 12px; font-size:12.5px; cursor:pointer; white-space:nowrap; }
.fm-purge button:hover { background:var(--accent); color:#fff; }
.fm-purge .hint { flex:1; min-width:170px; margin:0; }
/* 赞助 / 交流加群: 位于所有设置子页签之外, 切到任何子菜单都常驻可见 */
.support-box { margin-top:16px; border-top:1px solid var(--line2); padding-top:12px; }
.support-sec { margin-bottom:14px; }
.support-sec:last-child { margin-bottom:2px; }
.support-title { font-size:13px; font-weight:600; color:var(--text); margin-bottom:4px; }
.support-text { font-size:11.5px; color:var(--muted2); line-height:1.6; margin-bottom:8px; }
.support-qrs { display:flex; gap:10px; flex-wrap:wrap; }
.support-qrs figure { margin:0; flex:1 1 120px; min-width:0; max-width:150px; text-align:center; }
.support-qrs img { width:100%; aspect-ratio:1; object-fit:contain; border-radius:8px; border:1px solid var(--line2); background:#fff; display:block; }
.support-qrs figcaption { font-size:10.5px; color:var(--muted); margin-top:5px; line-height:1.4; }
.item .tag { font-size:10.5px; color:#4cc38a; }
.toast { position:fixed; bottom:24px; left:50%; transform:translateX(-50%); background:var(--accent); color:#fff; padding:9px 20px; border-radius:20px; font-size:13px; z-index:300; display:none; }
.expp { display:none; position:fixed; right:14px; bottom:70px; width:min(320px,92vw); background:var(--panel); border:1px solid var(--line2); border-radius:14px; padding:14px; z-index:150; box-shadow:0 10px 30px #00000060; }
.expp.open { display:block; }
.expp .expt { font-size:13.5px; font-weight:600; margin-bottom:10px; display:flex; align-items:center; }
.expp .expt .x { margin-left:auto; background:none; border:none; color:var(--muted); font-size:17px; cursor:pointer; }
.expitem { display:flex; align-items:center; gap:8px; padding:7px 4px; font-size:13px; color:var(--text); cursor:pointer; }
.expitem input { accent-color:var(--accent); width:16px; height:16px; }
.expr { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }
.expr .mini { flex:1 1 30%; padding:7px 0; border-radius:9px; background:var(--panel2); border:none; color:var(--text); font-size:12.5px; cursor:pointer; }
.expr .mini.primary { background:var(--accent); color:#fff; }
.stitch { display:none; position:fixed; inset:0; background:rgba(0,0,0,.72); z-index:260; align-items:center; justify-content:center; }
.stitch.open { display:flex; }
.stbox { width:min(340px,88vw); background:var(--panel); border-radius:16px; padding:20px 20px 16px; box-shadow:0 12px 40px #000c; text-align:center; }
.stt { font-size:14.5px; font-weight:600; margin-bottom:14px; }
.stbar { height:8px; border-radius:5px; background:var(--line2); overflow:hidden; }
.stfill { height:100%; width:0; background:linear-gradient(90deg,#3b82f6,#60a5fa); border-radius:5px; transition:width .4s; }
.stpct { font-size:13px; color:var(--muted); margin-top:8px; }
.sthint { font-size:11.5px; color:var(--muted2); margin-top:4px; line-height:1.5; }
.livep { display:none; position:fixed; inset:0; background:var(--bg, #10151c); z-index:240; flex-direction:column; }
.livep.open { display:flex; }
.lvtop { display:flex; align-items:center; gap:10px; padding:10px 14px; border-bottom:1px solid var(--line); flex:0 0 auto; }
.lvtitle { font-weight:600; font-size:15px; flex:1; min-width:0; line-height:1.35; }
.lvtitle small { display:block; font-size:12px; font-weight:400; }
.lvstatus { font-size:12px; color:var(--muted); text-align:right; max-width:42%; line-height:1.4; word-break:break-all; }
.lvbody { flex:1; overflow:auto; padding:12px 14px 20px; display:flex; flex-direction:column; gap:10px; }
.lvmeta { font-size:13px; color:var(--muted); }
#lv-video { width:100%; max-height:58vh; background:#000; border-radius:12px; }
/* 全览: 4 路同屏 */
.lvgrid { display:none; gap:3px; background:#000; border-radius:12px; overflow:hidden; grid-template-columns:1fr 1fr; }
.lvgrid.on { display:grid; }
.lvgrid.n1 { grid-template-columns:1fr; }
.lvgrid .lvcell { position:relative; overflow:hidden; background:#000; aspect-ratio:16/9; cursor:zoom-in; }
.lvgrid .lvcell video { width:100%; height:100%; object-fit:cover; display:block; background:#000; }
.lvgrid .lvcell .clabel { position:absolute; right:6px; bottom:6px; font-size:11px; color:#e8eef4; background:rgba(20,24,29,.72); border-radius:6px; padding:2px 7px; pointer-events:none; }
.lvrow { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.lvbtn { padding:9px 14px; border-radius:9px; border:1px solid var(--line2); background:var(--panel2); color:var(--text); font-size:13px; cursor:pointer; }
.lvbtn.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.lvrow select { padding:9px 10px; border-radius:9px; border:1px solid var(--line2); background:var(--panel2); color:var(--text); font-size:13px; }
.lvbody > .hint { margin-top:2px; margin-bottom:0; }
@media (max-width:640px){ .expp { left:50%; right:auto; transform:translateX(-50%); bottom:78px; } }
</style><script>
/* 版本自检: 服务端升级后, 若当前页面仍是旧版本就自动重载 (看片时不打断, 最多 2 次防死循环) */
(function () {
  var MY = "__APP_VER__";
  function chk() {
    if (document.querySelector('.player.open')) return;
    var k = 'tsf_ver_chk', o = {};
    try { o = JSON.parse(sessionStorage.getItem(k) || '{}') || {}; } catch (e) { o = {}; }
    if (!o.t || Date.now() - o.t > 600000) o = { t: Date.now(), n: 0 };
    if ((o.n || 0) >= 2) return;
    fetch('/api/ver?t=' + Date.now(), { cache: 'no-store', credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.v && j.v !== MY) {
          o.n = (o.n || 0) + 1;
          try { sessionStorage.setItem(k, JSON.stringify(o)); } catch (e) {}
          location.reload();
        }
      }).catch(function () {});
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) setTimeout(chk, 300); });
  window.addEventListener('load', function () { setTimeout(chk, 1200); });
})();
</script></head><body>
<header><h1>🚗 特斯拉哨兵查看器</h1><span class="sub" id="stat"></span>
<span id="live" title="车机在线只读同步状态" style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:4px 10px;white-space:nowrap"><span id="livedot" style="width:7px;height:7px;border-radius:50%;background:#5f6b76"></span><span id="livetxt">同步状态</span></span>
<nav class="tabs" id="tabs"></nav><button id="theme" title="主题: 自动/浅色/深色" onclick="cycleTheme()">🌗</button><button id="gear" title="设置">⚙</button></header>
<nav class="storwrap" id="storwrap" style="display:none">
  <div class="storcard" id="storcard">
<div class="storrow"><span class="storlab">虚拟U盘</span><span class="storval" id="storvusb"></span></div>
<div class="storbar"><i id="storvusbbar"></i></div>
<div class="storextra" id="storextra">
<div class="storrow"><span class="storlab">归档区</span><span class="storval" id="storarc"></span></div>
<div class="storbar"><i id="storarcbar"></i></div>
<div class="storrow"><span class="storlab">总占用</span><span class="storval" id="stortot"></span></div>
<div class="storbar"><i id="stortotbar"></i></div>
</div>
<button class="stortog" id="stortog" onclick="toggleStor()" title="展开归档区和总占用" aria-expanded="false" aria-controls="storextra"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9.5l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
  </div>
</nav>
<nav class="filters" id="filters"></nav>
<nav class="filters" id="dfilters"></nav>
<input type="date" id="dpick" class="dpick" onchange="if(this.value) setDate(this.value)">
<div class="grid-wrap" id="grid"></div>
<div class="empty" id="empty" style="display:none"><div class="big">📹</div>
<div>暂无归档视频<br><small style="font-size:12px">车机录制后, 归档同步任务会自动把视频收进来 (镜像需空闲10分钟)</small></div></div>

<div class="player" id="player">
  <div class="ptop">
    <button class="pback" onclick="closePlayer()">‹ 返回</button>
    <span class="ptitle" id="ptitle"></span>
    <button class="pdl" onclick="openExport()" title="导出">⬇</button>
  </div>
  <div class="pscroll">
    <div class="tpanel" id="tpanel">
        <div class="tp-top">
          <span class="tp-l"><span class="tp-date" id="tp-date">—</span><span class="tp-loc" id="tp-loc"></span><span class="tp-tag" id="tp-tag"></span></span>
          <span class="tp-tr"><span class="tp-sig" id="tp-bl" title="左转向灯"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="0.9" y="0.9" width="22.2" height="22.2" rx="6.2" fill="currentColor"/><path d="M17 12H8.2M12.2 7.3 7.5 12l4.7 4.7" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="tp-sig" id="tp-br" title="右转向灯"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="0.9" y="0.9" width="22.2" height="22.2" rx="6.2" fill="currentColor"/><path d="M7 12h8.8M11.8 7.3 16.5 12l-4.7 4.7" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="tp-gear" id="tp-gear">D</span></span>
        </div>
        <div class="tp-mid">
          <span class="tp-spd" id="tp-spd">0</span><span class="tp-unit">km/h</span>
          <span class="tp-badges">
            <span class="tp-badge brk" id="tp-brk" title="刹车 (数据源只有踩下/未踩, 无踏板开度)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="7.6"/><circle cx="12" cy="12" r="2.5"/><path d="M17.4 6.6l2.7-2.7"/></svg><b>刹车</b></span>
            <span class="tp-badge thr" id="tp-thr" title="油门"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M9.4 3.6h5.2a1.7 1.7 0 0 1 1.68 1.93l-1.35 12.3a1.8 1.8 0 0 1-1.79 1.6h-2.28a1.8 1.8 0 0 1-1.79-1.6L7.72 5.53A1.7 1.7 0 0 1 9.4 3.6z"/><path d="M8.6 15.1h6.8"/></svg><b id="tp-thr-n">0%</b></span>
            <span class="tp-c st" id="tp-st" title="方向盘角度"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.6"/><path d="M3.2 10.5h6M14.8 10.5h6M12 14.6V21"/></svg></span>
            <span class="tp-c ap" id="tp-ap" style="display:none">AP</span>
          </span>
        </div>
        <button class="tp-fold" id="tp-fold" onclick="toggleTelRows()">收起详细数据 ⌃</button>
        <div class="tp-rows" id="tp-rows"></div>
    </div>
    <div class="vwrap">
      <div class="startmask" id="startmask">▶ 轻触开始同步播放</div>
      <div class="vgrid" id="vgrid"></div>
    </div>
    <div class="infocard" id="infocard">
      <div class="ic-top"><span class="ic-time" id="ic-time">—</span><span class="ic-badge" id="ic-cat">—</span></div>
      <div class="ic-mid"><span class="ic-big" id="ic-big">—</span><span class="ic-sub" id="ic-sub"></span></div>
      <div class="clipbar" id="clipbar"></div>
      <button class="ic-fold" id="ic-fold" onclick="toggleDetail()">收起详细数据 ⌃</button>
      <div class="ic-rows" id="ic-rows"></div>
    </div>
  </div>
  <div class="pbar">
    <button class="pp" id="ppbtn" onclick="togglePlay()">⏸</button>
    <input type="range" id="seek" min="0" max="1000" value="0">
    <span class="t" id="ptime">00:00</span>
    <button class="spd" id="spdbtn" onclick="cycleSpeed()">1x</button>
    <div class="qsel" id="qsel"></div>
    <div class="qhint" id="qhint"></div>
  </div>
</div>

<div class="expp" id="expp">
  <div class="expt">选择要导出的画面<button class="x" onclick="closeExport()">✕</button></div>
  <div id="expchks"></div>
  <label class="expitem" id="expwm-row" style="border-top:1px solid var(--line2);margin-top:6px;align-items:flex-start">
    <input type="checkbox" id="expwm" style="margin-top:2px">
    <span style="line-height:1.45">叠加行车信息水印<small style="display:block;color:#7d8894;font-size:11px;margin-top:2px">半透明单行: 时间/位置/车速/挡位/油门/刹车, 基本不挡画面</small></span>
  </label>
  <div class="expitem" id="expq-row" style="border-top:1px solid var(--line2);margin-top:6px">
    <span style="flex:0 0 auto;color:#c6cdd4;cursor:default">下载清晰度</span>
    <div class="qsel" id="expq" style="flex:1;display:flex;justify-content:flex-end"></div>
  </div>
  <div class="expr">
    <button class="mini" onclick="expChk(true)">全选</button>
    <button class="mini" onclick="expChk(false)">清空</button>
    <button class="mini" onclick="doExportZip()">打包zip</button>
    <button class="mini" onclick="doPushFeishu()">推送飞书</button>
    <button class="mini primary" onclick="doExport()">合成视频</button>
  </div>
  <div class="hint" style="margin-top:8px;margin-bottom:0">单选取原文件直接下载(加水印时走本机合成)；多选合成一个多画面视频。下载/打包按所选清晰度导出(首次需等转码)。「推送飞书」按所选画面+水印合成后发到你的飞书私聊 (需已配置推送, 首次合成约1-3分钟)。</div>
</div>

<div class="mask" id="mask" onclick="closePanel()"></div>

<div class="livep" id="livep">
  <div class="lvtop">
    <button class="pback" onclick="closeLive()">‹ 返回</button>
    <span class="lvtitle">📡 准实时预览 <small style="color:#7d8894;font-weight:400">延时约 1~2 分钟</small></span>
    <span class="lvstatus" id="lv-status"></span>
  </div>
  <div class="lvbody">
    <div class="lvmeta" id="lv-name">—</div>
    <video id="lv-video" controls playsinline muted></video>
    <div class="lvgrid" id="lv-grid"></div>
    <div class="lvrow" id="lv-cams"></div>
    <div class="lvrow">
      <span style="font-size:13px;color:var(--muted)">刷新检查</span>
      <select id="lv-iv" onchange="liveRestart()">
        <option value="15">15 秒</option>
        <option value="30">30 秒</option>
        <option value="60">1 分钟</option>
        <option value="0">仅手动</option>
      </select>
      <button class="lvbtn" onclick="liveTick()">检查更新</button>
    </div>
    <div class="hint">「全览」同时播放全部摄像头原画, 数据量较大 (4 路每分钟约 100MB), 建议流量充足时使用; 点任一格可切到单路。原理: 车机循环录制 1 分钟一段; 后台的只读同步 (不打断车机) 把刚写完的片段持续收进归档, 这里自动发现并播放最新片段, 即约 1 分钟延时的"直播"。刚停车/刚触发录像的片段要等写完才会出现。</div>
  </div>
</div>
<div class="panel" id="panel">
  <h2>⚙ 个性化 <button class="x" onclick="closePanel()">✕</button></h2>
  <div class="stabs">
    <button class="stab active" id="st-files" onclick="stab('files')">📂 文件</button>
    <button class="stab" id="st-audio" onclick="stab('audio')">🔊 锁车语音</button>
    <button class="stab" id="st-wraps" onclick="stab('wraps')">🚙 涂装</button>
    <button class="stab" id="st-notify" onclick="stab('notify')">🔔 推送</button>
    <button class="stab" id="st-auth" onclick="stab('auth')">🔑 账号</button>
    <button class="stab" id="st-lang" onclick="stab('lang')">🌐 语言</button>
    <button class="stab" id="st-skin" onclick="stab('skin')">🎨 网页背景</button>
    <button class="stab" id="st-update" onclick="stab('update')">🔄 更新</button>
  </div>
  <div id="tab-skin" style="display:none">
    <div class="hint">上传 CSS 皮肤文件或背景图 (jpg/png/webp)。CSS 中引用图片请用 /skin-media/文件名。</div>
    <div class="hint"><code>body{background:url(/skin-media/bg.jpg) center/cover}</code></div>
    <label class="upbtn">＋ 上传皮肤文件 (css/jpg/png/webp)<input type="file" id="skin-file" accept=".css,.jpg,.jpeg,.png,.webp" hidden></label>
    <div id="skin-list"></div>
  </div>
  <div id="tab-audio" style="display:none">
    <div class="hint">上传的音频 (wav/mp3, &lt;1MB) 会直接写入车机U盘根目录；文件名为 LockChime.wav 的即锁车音 (特斯拉只认根目录这个文件名)。文件在归档同步时写入车机U盘。</div>
    <label class="upbtn">＋ 上传音频 (wav/mp3, &lt;1MB)<input type="file" id="audio-file" accept=".wav,.mp3" hidden></label>
    <div class="hint" style="color:#4cc38a">⏳ 待写入车机U盘:</div>
    <div id="audio-list"></div>
    <div class="hint" style="color:#4cc38a">✅ 已写入:</div>
    <div id="audio-done"></div>
  </div>
  <div id="tab-wraps" style="display:none">
    <div class="hint">车机涂装/牌照图片: 上传 png/jpg/webp, 直接写入车机U盘根目录 Wraps/ (车机端设置里选)。连接时会临时断开车机读取几秒, 用完点「恢复车机导出」; 5 分钟无操作也会自动恢复。</div>
    <div id="wraps-status" style="margin-bottom:10px"></div>
    <label class="upbtn" id="wraps-upbtn">＋ 上传图片到 Wraps/ (png/jpg/webp, ≤100MB)<input type="file" id="wraps-file" accept=".png,.jpg,.jpeg,.webp" hidden multiple></label>
    <div id="wraps-grid" style="display:flex;flex-wrap:wrap;gap:10px;margin-top:12px"></div>
  </div>
  <div id="tab-files">
    <div style="display:flex;gap:8px;margin-bottom:10px">
      <button id="area-usb" class="stab active" style="flex:1" onclick="switchArea('usb')">🚗 车机U盘</button>
      <button id="area-archive" class="stab" style="flex:1" onclick="switchArea('archive')">🗄 归档区</button>
    </div>
    <div id="usb-status" style="display:none;margin-bottom:10px"></div>
    <div id="usb-hint" class="hint">浏览和管理车机U盘 (特斯拉写入的镜像)。连接后会临时断开车机读取几秒，5 分钟无操作自动恢复。</div>
    <div id="arch-hint" class="hint" style="display:none">归档区 (F50 内部存储): 查看器用的视频副本、皮肤、文件中转。</div>
    <div id="purge-box" class="fm-purge">
      <button onclick="purgeTeslaCam()">🗑 清空 TeslaCam 全部录像</button>
      <span class="hint">仅删除 TeslaCam/ 内所有视频与事件文件夹，保留目录结构；Boombox、Wraps 等其他根目录不受影响。需先连接 U 盘。</span>
    </div>
    <div id="dir-nav" style="display:none">
      <div style="display:flex;gap:8px;margin-bottom:10px">
        <input id="dest-dir" placeholder="/" style="flex:1;background:#161c22;border:1px solid #3a4652;border-radius:8px;color:#e8eaed;padding:8px 10px;font-size:12px;outline:none">
        <button onclick="goDir(document.getElementById('dest-dir').value)" style="background:#e8443f;border:none;color:#fff;border-radius:8px;padding:0 14px;font-size:12px;cursor:pointer">跳转</button>
      </div>
      <div id="crumb" style="font-size:12px;color:#5ea9ff;margin-bottom:8px;line-height:1.8"></div>
      <label class="upbtn" id="upbtn">＋ 上传文件到当前目录 (任意类型, ≤100MB)<input type="file" id="anyfile-file" hidden></label>
      <div id="fm-bar" class="fm-bar">
        <div class="row" style="justify-content:space-between;margin-bottom:8px">
          <label class="row" style="cursor:pointer;font-size:12.5px;gap:6px">
            <input type="checkbox" id="fm-all" style="width:17px;height:17px;accent-color:var(--accent);cursor:pointer" onchange="fmAll(this.checked)"> 全选
          </label>
          <span class="fm-count" id="fm-count">已选 0 项</span>
        </div>
        <div class="row">
          <button class="fm-danger" onclick="fmOp('delete')">🗑 删除</button>
          <button onclick="fmOp('move')">📦 移动</button>
          <button onclick="fmOp('copy')">📋 复制</button>
          <button onclick="fmOp('rename')">✏️ 重命名</button>
        </div>
        <div class="row" id="fm-dest" style="display:none;margin-top:8px">
          <input class="fm-dest-input" id="fm-dest-input" placeholder="目标目录">
          <button class="fm-go" onclick="fmDestGo()">执行</button>
          <button onclick="fmDestCancel()">取消</button>
        </div>
      </div>
      <div id="file-list"></div>
    </div>
  </div>
  <div id="tab-notify" style="display:none">
    <div class="hint">哨兵触发时把时间/位置/触发原因和触发画面推到飞书。默认走自建应用私聊(只有你本人可见)；也可切到群机器人 webhook(群里都能看到)。视频推送只支持私聊。</div>
    <label class="nrow"><span>启用推送</span><input type="checkbox" id="nt-en"></label>
    <label class="nrow"><span>也推手动保存</span><input type="checkbox" id="nt-saved"></label>
    <select class="ninput" id="nt-mode">
      <option value="dm">私聊推送 — 只有我本人能看到</option>
      <option value="group">群机器人 — 群里所有人都能看到</option>
    </select>
    <div class="eyebar">
      <span class="lbl" id="nt-eyetip">敏感信息已隐藏（截图安全）</span>
      <button class="mini" id="nt-eye" onclick="ntToggleEye()">👁 显示</button>
    </div>
    <input class="ninput" id="nt-to" placeholder="接收人: 你的 open_id (ou_ 开头) 或飞书登录邮箱">
    <select class="ninput" id="nt-media" onchange="ntMediaUI()">
      <option value="thumb">推送内容：封面卡片（触发那帧图 + 信息，最快）</option>
      <option value="video">推送内容：触发摄像头视频（可回放）</option>
    </select>
    <div id="nt-vwrap" style="display:none">
      <select class="ninput" id="nt-quality">
        <option value="720p">视频清晰度：720p（约 5MB / 30 秒）</option>
        <option value="480p">视频清晰度：480p（约 3MB / 30 秒）</option>
        <option value="360p">视频清晰度：360p（约 1.8MB / 30 秒）</option>
      </select>
      <div class="nrow"><span>视频时长</span><input class="ninput num" id="nt-vdur" type="number" min="10" max="60"> <span class="hint2">秒（从触发前 5 秒开始截）</span></div>
    </div>
    <input class="ninput" id="nt-appid" placeholder="App ID — cli_ 开头">
    <input class="ninput" id="nt-secret" type="password" placeholder="App Secret (已保存则留空表示不修改)">
    <input class="ninput" id="nt-hook" placeholder="群机器人 webhook (仅群推送需要): https://open.feishu.cn/open-apis/bot/v2/hook/...">
    <input class="ninput" id="nt-url" placeholder="查看链接前缀 (可选): http://你的域名:端口">
    <div class="nrow"><span>延迟推送</span><input class="ninput num" id="nt-delay" type="number" min="5" max="600"> <span class="hint2">秒 (等片段写完再推)</span></div>
    <div class="updbtns">
      <button class="mini primary" onclick="ntSave()">保存设置</button>
      <button class="mini" onclick="ntTest()">发送测试</button>
    </div>
    <div class="hint" id="nt-msg"></div>
  </div>
  <div id="tab-auth" style="display:none">
    <div class="hint">改完本机会自动用新密码重新登录，手机/其它设备上的登录状态立即失效。</div>
    <input class="ninput" id="pw-old" type="password" placeholder="当前密码" autocomplete="current-password">
    <input class="ninput" id="pw-new" type="password" placeholder="新密码 (至少 6 位)" autocomplete="new-password">
    <input class="ninput" id="pw-new2" type="password" placeholder="再输一次新密码" autocomplete="new-password">
    <div class="updbtns"><button class="mini primary" onclick="pwSave()">修改密码</button></div>
    <div class="hint" id="pw-msg"></div>
    <div class="hint" style="margin-top:12px"><span>用户名</span>: <b id="pw-user">admin</b></div>
    <div class="hint">要改用户名请编辑设备上 /opt/tesla-sentry/auth.conf 的 user=</div>
    <div class="updbtns" style="margin-top:16px"><button class="mini logout" onclick="doLogout()">退出登录</button></div>
  </div>
  <div id="tab-update" style="display:none">
    <div class="updrow"><span class="updk">当前版本</span><b id="upd-cur">—</b></div>
    <div class="updrow"><span class="updk">最新版本</span><b id="upd-new">—</b><span class="updtag" id="upd-tag">未检查</span></div>
    <div class="updnotes" id="upd-notes"></div>
    <label class="updauto" title="检测到新版本时, 在没人看视频的空闲时刻自动升级 (只重启本服务, 约 5 秒)">
      <span>自动更新</span>
      <input type="checkbox" id="upd-auto" checked onchange="setAutoUpdate(this.checked)">
    </label>
    <div class="hint" id="upd-autohint">—</div>
    <div class="updbtns">
      <button class="mini" id="upd-check" onclick="otaCheck(true)">检查更新</button>
      <button class="mini primary" id="upd-go" onclick="otaApply()" disabled>立即升级</button>
    </div>
    <div class="hint" id="upd-msg"></div>
    <div class="updbtns"><button class="mini" id="upd-rb" onclick="otaRollback()" style="display:none">↩ 回滚到上一版本</button></div>
    <div class="hint">升级会从 GitHub 及多个镜像并发竞速拉取最新程序, 覆盖本机文件并自动重启服务 (约5秒); 配置、录像、登录信息不受影响。</div>
  </div>
  <div id="tab-lang" style="display:none">
    <div class="hint" style="margin-bottom:8px">选择语言</div>
    <div class="langgrid" id="lang-grid"></div>
    <div class="hint">界面语言，保存在本机浏览器</div>
  </div>
  <!-- 常驻模块: 在 stab 控制的 tab 容器之外, 所有设置子菜单下都可见 -->
  <div class="support-box" id="support-box">
    <div class="support-sec">
      <div class="support-title">💬 交流加群</div>
      <div class="support-text">交流加群请联系作者，扫码关注抖音或添加微信好友~</div>
      <div class="support-qrs">
        <figure><img src="/sponsor/douyin?v=__SPONSOR_VER__" alt="抖音二维码" loading="lazy"><figcaption>抖音 · @耶基大学懂事长</figcaption></figure>
        <figure><img src="/sponsor/wechatauthor?v=__SPONSOR_VER__" alt="作者微信二维码" loading="lazy"><figcaption>微信 · 联系作者</figcaption></figure>
      </div>
    </div>
    <div class="support-sec">
      <div class="support-title">☕ 赞助</div>
      <div class="support-text">本项目完全开源免费，如果它帮到了你，欢迎支持博主一些token，助力项目持续更新~</div>
      <div class="support-qrs">
        <figure><img src="/sponsor/alipay?v=__SPONSOR_VER__" alt="支付宝收款码" loading="lazy"><figcaption>支付宝</figcaption></figure>
        <figure><img src="/sponsor/wechatpay?v=__SPONSOR_VER__" alt="微信收款码" loading="lazy"><figcaption>微信支付</figcaption></figure>
      </div>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<div class="stitch" id="stitch">
  <div class="stbox">
    <div class="stt">正在合成多画面视频</div>
    <div class="stbar"><div class="stfill" id="stfill"></div></div>
    <div class="stpct" id="stpct">0%</div>
    <div class="sthint" id="sthint">设备正在拼接各路画面, 请勿关闭页面</div>
  </div>
</div>

<script>window.__I18N=__I18N__;window.__LANGS=__LANGS__;</script>
<script>
let data = {}, cur = null, syncTimer = null, currentCat = 'SentryClips', lastSig = '', reasonFilter = 'all', dateFilter = 'all';
let curClips = [], curClipIx = 0, TEL = null;

/* ---------- 多语言 ----------
   字典以中文原文为 key; 静态文案由 applyLang() 遍历 DOM 替换, 动态文案在生成时用 t() 包一层。
   切回中文时从节点上保存的原文还原, 不会叠加翻译。 */
const I18N = window.__I18N || {};
const LANGS = window.__LANGS || [['zh', '中文']];
let LANG = (localStorage.getItem('tsf_lang') || '').trim();
if (!LANGS.some(l => l[0] === LANG)) {
  const nav = String((navigator.languages && navigator.languages[0]) || navigator.language || 'zh').toLowerCase();
  const hit = LANGS.find(l => nav === l[0] || nav.indexOf(l[0] + '-') === 0);
  LANG = hit ? hit[0] : 'en';
}
function t(s) {
  if (!s || LANG === 'zh') return s;
  const m = I18N[LANG] || {};
  return m[s] || (I18N.en || {})[s] || s;
}
function langName(code) {
  const l = LANGS.find(x => x[0] === code);
  return l ? l[1] : code;
}
const ZH_TXT = '__zh', ZH_TV = '__tv', ZH_ATTR = '__zha';
function applyLang() {
  document.documentElement.lang = (LANG === 'zh') ? 'zh-CN' : LANG;
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
  const nodes = [];
  while (w.nextNode()) nodes.push(w.currentNode);
  for (const n of nodes) {
    const p = n.parentNode;
    if (!p) continue;
    const tag = p.nodeName;
    if (tag === 'SCRIPT' || tag === 'STYLE') continue;
    const cur = n.nodeValue;
    if (n[ZH_TXT] !== undefined && cur !== n[ZH_TV] && cur !== n[ZH_TXT]) n[ZH_TXT] = cur;
    if (n[ZH_TXT] === undefined) n[ZH_TXT] = cur;
    const raw = n[ZH_TXT], key = (raw || '').trim();
    if (!key) continue;
    const val = t(key);
    n.nodeValue = (val === key) ? raw : raw.replace(key, val);
    n[ZH_TV] = n.nodeValue;
  }
  document.querySelectorAll('[placeholder],[title]').forEach(el => {
    ['placeholder', 'title'].forEach(a => {
      if (!el.hasAttribute(a)) return;
      el[ZH_ATTR] = el[ZH_ATTR] || {};
      if (el[ZH_ATTR][a] === undefined) el[ZH_ATTR][a] = el.getAttribute(a);
      const raw = el[ZH_ATTR][a], key = (raw || '').trim();
      if (!key) return;
      const val = t(key);
      el.setAttribute(a, val === key ? raw : raw.replace(key, val));
    });
  });
  const g = document.getElementById('lang-grid');
  if (g) renderLangGrid();
}
function renderLangGrid() {
  const g = document.getElementById('lang-grid');
  if (!g) return;
  g.innerHTML = LANGS.map(([c, n]) =>
    '<button class="' + (c === LANG ? 'on' : '') + '" onclick="setLang(\'' + c + '\')">' +
    n + '<span class="code">' + c.toUpperCase() + '</span></button>').join('');
}
function setLang(code) {
  if (!LANGS.some(l => l[0] === code)) return;
  LANG = code;
  localStorage.setItem('tsf_lang', code);
  applyLang();
  const th = localStorage.getItem('tsf_theme') || 'auto';
  const b = document.getElementById('theme');
  if (b) b.title = t('主题') + ': ' + t(th === 'auto' ? '自动' : th === 'light' ? '浅色' : '深色');
  if (typeof data !== 'undefined' && data && document.getElementById('tabs')) { try { load(); } catch (e) {} }
}

/* ---------- 主题: 自动(跟系统) / 浅色 / 深色, 手动循环切换并记住 ---------- */
const THEME_ICONS = { auto: '🌗', light: '☀️', dark: '🌙' };
const THEME_NEXT = { auto: 'light', light: 'dark', dark: 'auto' };
function applyTheme(mode) {
  let eff = mode;
  if (mode === 'auto') {
    eff = window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.dataset.theme = eff;
  const b = document.getElementById('theme');
  if (b) { b.textContent = THEME_ICONS[mode] || '🌗'; b.title = t('主题') + ': ' + t(mode === 'auto' ? '自动' : mode === 'light' ? '浅色' : '深色'); }
}
function cycleTheme() {
  const cur = localStorage.getItem('tsf_theme') || 'auto';
  const nxt = THEME_NEXT[cur] || 'auto';
  localStorage.setItem('tsf_theme', nxt);
  applyTheme(nxt);
}
applyTheme(localStorage.getItem('tsf_theme') || 'auto');
/* ---------- 存储卡展开/收起 ----------
   默认只显示「虚拟U盘」一行; 点卡片右下角小箭头才展开「归档区 / 总占用」 */
function toggleStor() {
  const c = document.getElementById('storcard'), b = document.getElementById('stortog');
  if (!c || !b) return;
  const open = c.classList.toggle('open');
  b.title = open ? '收起归档区和总占用' : '展开归档区和总占用';
  b.setAttribute('aria-expanded', open ? 'true' : 'false');
}
if (window.matchMedia) matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if ((localStorage.getItem('tsf_theme') || 'auto') === 'auto') applyTheme('auto');
});
/* ---------- 清晰度选择 ----------
   内网(100.96.x/192.168.x)默认原画; 走公网/穿透访问时默认 360p 省 5G 上行带宽 */
const Q_LIST = [['orig', '原画'], ['720p', '720p'], ['480p', '480p'], ['360p', '360p']];
const isLanNet = /^(100\.96\.|192\.168\.|10\.|127\.)/.test(location.hostname);
let curQ = localStorage.getItem('tsfq') || (isLanNet ? 'orig' : '360p');
/* 苹果设备: 合成改走设备端 (Safari 的 MediaRecorder 只能出 webm, 微信不认) */
const IS_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
               (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
if (!Q_LIST.some(x => x[0] === curQ)) curQ = 'orig';
function qUrl(u) { return curQ === 'orig' ? u : u + '?q=' + curQ; }
function renderQSel() {
  const curLabel = (Q_LIST.find(x => x[0] === curQ) || Q_LIST[0])[1];
  document.getElementById('qsel').innerHTML =
    `<button class="qbtn ${curQ !== 'orig' ? 'on' : ''}" onclick="toggleQ(event)">${curLabel} ⌃</button>` +
    '<div class="qmenu">' + Q_LIST.map(([k, label]) =>
      `<button class="${k === curQ ? 'on' : ''}" onclick="setQuality('${k}')">${label}</button>`).join('') + '</div>';
}
function toggleQ(e) {
  if (e) e.stopPropagation();
  document.getElementById('qsel').classList.toggle('open');
}
function closeQ() {
  ['qsel', 'expq'].forEach(id => { const el = document.getElementById(id); if (el) el.classList.remove('open'); });
}
document.addEventListener('click', closeQ);
function setQuality(q) {
  closeQ();
  if (q === curQ) return;
  curQ = q; localStorage.setItem('tsfq', q);
  renderQSel();
  if (!cur) return;
  // 记住各路播放进度与播放状态, 换源后续播
  const v0el = document.querySelector('#vgrid video');
  const wasPlaying = v0el ? !v0el.paused : false;
  const times = [...document.querySelectorAll('#vgrid video')].map(v => v.currentTime || 0);
  const hint = document.getElementById('qhint');
  if (q !== 'orig') {
    hint.textContent = '正在按 ' + q + ' 转码所选片段 (首次约需 20~60 秒/路, 转好一次后秒开)…';
    hint.style.display = 'block';
    clearTimeout(setQuality._ht);
    setQuality._ht = setTimeout(() => { hint.style.display = 'none'; }, 90000);
  } else {
    hint.style.display = 'none';
  }
  renderClip();
  const vids = [...document.querySelectorAll('#vgrid video')];
  vids.forEach((v, i) => {
    const seek = () => { try { if (times[i]) v.currentTime = times[i]; } catch (e) {} };
    if (v.readyState >= 1) seek();
    else v.addEventListener('loadedmetadata', seek, { once: true });
  });
  if (wasPlaying) tryPlay();
  // 第一路能播就撤提示
  const v0 = vids[0];
  if (v0) v0.addEventListener('canplay', () => { hint.style.display = 'none'; }, { once: true });
}
const CATS_NAME = __CAT_NAMES__;
/* event.json 里的 reason → 中文名 (reason 是自由文本, 新值靠前缀匹配兜底) */
const REASON_LABEL = {
  sentry_aware_object_detection: '哨兵警觉',
  sentry_aware_door: '车门拉动',
  sentry_aware_handle: '车门拉动',
  sentry_aware_accel: '遭遇袭击',
  sentry_panic: '遭遇袭击',
  sentry_trigger: '哨兵模式触发',
  user_interaction_dashcam_saved: '手动保存',
  user_interaction_dashcam_menu: '行车记录仪菜单保存',
  user_interaction_dashcam_launcher_action_tapped: '点击行车记录仪图标',
  user_interaction_honk: '按喇叭保存'
};
function reasonLabel(r) {
  if (!r) return '';
  if (REASON_LABEL[r]) return t(REASON_LABEL[r]);
  for (const k in REASON_LABEL) if (r.indexOf(k) === 0) return t(REASON_LABEL[k]);
  return r;
}
const EMPTY_HTML = document.getElementById('empty').innerHTML;
/* Model 3/Y (HW3) 只有 4 路: front / back / left_repeater / right_repeater
   Model S/X 与 HW4 车型才有 6 路 (含 left_pillar / right_pillar)
   页面按每个事件实际的文件数自适应, 不写死路数 */
const CAM_NAME = { front:'前视', back:'后视', left_repeater:'左B柱', right_repeater:'右B柱',
                   left_pillar:'左前翼', right_pillar:'右前翼' };
/* 排列顺序按「位置成对」：
   4 路(3/Y)      第1排 前视 后视 / 第2排 左B柱 右B柱
   6 路(S/X,HW4)  第1排 左前翼 前视 右前翼 / 第2排 左B柱 后视 右B柱
   这样左右对称, 前后同一排, 一眼能看出方位 */
const CAM_ORDER_4 = ['front','back','left_repeater','right_repeater','left_pillar','right_pillar'];
const CAM_ORDER_6 = ['left_pillar','front','right_pillar','left_repeater','back','right_repeater'];
function camLabel(c) { return t(CAM_NAME[c] || c || '画面'); }
function fmtFull(n) {
  const m = (n || '').match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})/);
  return m ? `${m[1]}/${m[2]}/${m[3]} ${m[4]}:${m[5]}:00` : n;
}
function camShort(c) {
  const m = {front:'前',back:'后',left_repeater:'左',right_repeater:'右',left_pillar:'左',right_pillar:'右'};
  return m[c] || (camLabel(c) || '画').slice(0, 1);
}
function toggleDetail() {
  const r = document.getElementById('ic-rows'), b = document.getElementById('ic-fold');
  const open = r.classList.toggle('open');
  b.textContent = open ? '收起详细数据 ⌃' : '展开详细数据 ⌄';
  layoutPlayer();
}
function fmtName(n) {
  const m = (n || '').match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})/);
  return m ? m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5] : n;
}
function camSort(vs) {
  const order = vs.length > 4 ? CAM_ORDER_6 : CAM_ORDER_4;
  return vs.slice().sort((a, b) => {
    const ia = order.indexOf(a.cam), ib = order.indexOf(b.cam);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
}
/* 事件目录里通常有多组片段 (每分钟一组, 每组 4~6 路), 按组切开 */
function clipKeyOf(v) {
  const f = v.f || v.url.split('/').pop();
  return f.replace(/\.mp4$/i, '').replace(/-[^-]+$/, '');
}
function groupVideos(vids) {
  const m = new Map();
  for (const v of camSort(vids || [])) {
    const k = clipKeyOf(v);
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(v);
  }
  return [...m.keys()].sort().map(k => ({ key: k, vids: m.get(k) }));
}
function curVids() { return (curClips[curClipIx] || { vids: [] }).vids; }
function switchClip(i) {
  if (!curClips[i]) return;
  curClipIx = i;
  renderClip();
}
function gridDims(n) {
  if (n <= 1) return [1, 1];
  if (n === 2) return [2, 1];
  if (n <= 4) return [2, 2];
  if (n <= 6) return window.innerWidth <= 700 ? [2, 3] : [3, 2];
  const c = window.innerWidth <= 700 ? 2 : 3;
  return [c, Math.ceil(n / c)];
}

async function load() {
  data = await (await fetch('/api/events')).json();
  const tabs = document.getElementById('tabs');
  tabs.innerHTML = '';
  const liveOpen = document.getElementById('livep').classList.contains('open');
  for (const c of Object.keys(data)) {
    const b = document.createElement('button');
    b.className = 'tab' + (!liveOpen && c === currentCat ? ' active' : '');
    b.innerHTML = `<span class="tl">${t(CATS_NAME[c] || c)}</span><span class="tn">${data[c].length}</span>`;
    b.onclick = () => { document.querySelectorAll('.tab').forEach(t => t.classList.remove('active')); b.classList.add('active'); currentCat = c; reasonFilter = 'all'; dateFilter = 'all'; closeLive(); render(c); };
    tabs.appendChild(b);
  }
  /* 第 4 个页签: 实时预览 (与分类页签同级) */
  const lv = document.createElement('button');
  lv.className = 'tab' + (liveOpen ? ' active' : '');
  lv.id = 'tab-live';
  lv.innerHTML = `<span class="tl">${t('实时预览')}</span>`;   // 与其它页签同结构 (单行分段控件里才不会错位)
  lv.onclick = () => { document.querySelectorAll('.tab').forEach(x => x.classList.remove('active')); lv.classList.add('active'); openLive(); };
  tabs.appendChild(lv);
  document.getElementById('stat').textContent = `${t('归档')} ${Object.values(data).reduce((a, v) => a + v.length, 0)} ${t('条')}`;
  lastSig = sigOf(data);
  renderQSel();
  render(currentCat);
  openDeepLink();
}

/* 支持 ?ev=SentryClips/2026-11-04_12-30-00 直接打开某个事件 */
function openDeepLink() {
  const q = new URLSearchParams(location.search);
  /* ?date=2026-11-03 只看某一天 */
  const qd = q.get('date');
  if (/^\d{4}-\d{2}-\d{2}$/.test(qd || '')) { dateFilter = qd; render(currentCat); }
  /* ?cat=SavedClips 直接切到某个分类 */
  const qc = q.get('cat');
  if (qc && Object.keys(data).includes(qc)) {
    const tabs = document.getElementById('tabs');
    [...tabs.children].forEach(b => b.classList.remove('active'));
    const idx = Object.keys(data).indexOf(qc);
    if (tabs.children[idx]) tabs.children[idx].classList.add('active');
    currentCat = qc; reasonFilter = 'all'; render(qc);
  }
  const m = q.get('ev');
  if (!m) return;
  const keys = Object.keys(data);
  for (const c of keys) {
    const ev = (data[c] || []).find(e => e.name === m || (c + '/' + e.name) === m);
    if (!ev) continue;
    const tabs = document.getElementById('tabs');
    [...tabs.children].forEach(b => b.classList.remove('active'));
    const idx = keys.indexOf(c);
    if (tabs.children[idx]) tabs.children[idx].classList.add('active');
    currentCat = c; reasonFilter = 'all'; render(c); openPlayer(ev);
    const cam = new URLSearchParams(location.search).get('cam');
    if (cam) {
      const cell = document.querySelector(`#vgrid .vcell[data-cam="${cam}"]`);
      if (cell) focusCell(cell);
    }
    if (q.get('exp') === '1') setTimeout(openExport, 600);
    return;
  }
}

function sigOf(d) {
  return Object.keys(d).map(c => c + ':' + d[c].map(e => e.name + '/' + e.videos.length).join(',')).join('|');
}

/* 实时同步状态 + 新片段自动出现 */
async function refreshLive() {
  let live = null;
  try { live = await (await fetch('/api/live')).json(); } catch (e) { return; }
  const dot = document.getElementById('livedot'), txt = document.getElementById('livetxt');
  if (live.running) {
    dot.style.background = '#4cc38a';
    if (live.pending > 0) txt.textContent = `${t('实时同步中')} · ${live.pending} ${t('待确认')}`;
    else if (live.ago !== null && live.ago !== undefined) txt.textContent = `${t('实时同步中')} · ${live.ago}s`;
    else txt.textContent = t('实时同步中');
  } else {
    dot.style.background = '#e8443f';
    txt.textContent = t('同步服务未运行');
  }
  // 存储卡: 默认只显示虚拟U盘, 点右下角小箭头再展开归档区/总占用
  const sw = document.getElementById('storwrap');  const dk = live.disk;
  if (dk && dk.disk_total > 0) {
    const GB = 1073741824;
    const gb = n => {
      if (!n || n < 0) return '0MB';
      return n >= GB ? (n / GB).toFixed(1) + 'GB' : Math.round(n / 1048576) + 'MB';
    };
    const tset = (v, used, total, bar, color) => {
      document.getElementById(v).textContent = total > 0 ? gb(used) + ' / ' + gb(total) : gb(used);
      const pct = total > 0 ? Math.min(100, used / total * 100) : 0;
      const el = document.getElementById(bar);
      el.style.width = pct.toFixed(1) + '%';
      // 到 90% 一律转红, 三行同规则
      el.style.background = pct >= 90 ? '#e8443f' : color;
    };
    sw.style.display = '';
    // 前两行各按自己的容量算, 第三行才是整盘
    // 虚拟U盘: 镜像文件实占 / 镜像标称容量
    tset('storvusb', dk.img_real, dk.img_nominal, 'storvusbbar', '#2f6ee0');
    // 归档区: 已归档 / 可用上限 (= 已归档 + 盘剩余)
    const arcTot = (dk.arc_used || 0) + (dk.arc_free || 0);
    tset('storarc', dk.arc_used, arcTot, 'storarcbar', '#4cc38a');
    // 总占用: 整盘已用 / 整盘容量 (三行都在 tset 里按 90% 自动转红)
    tset('stortot', dk.disk_used, dk.disk_total, 'stortotbar', '#2f6ee0');
  } else {
    sw.style.display = 'none';
  }
  // 事件有变化时无缝重绘 (不打断播放器)
  try {
    const d = await (await fetch('/api/events')).json();
    const s = sigOf(d);
    if (s !== lastSig) {
      lastSig = s; data = d;
      const tabs = document.getElementById('tabs');
      [...tabs.children].forEach((b, i) => {
        const c = Object.keys(d)[i];
        if (c) b.innerHTML = `<span class="tl">${t(CATS_NAME[c] || c)}</span><span class="tn">${d[c].length}</span>`;
      });
      document.getElementById('stat').textContent = `${t('归档')} ${Object.values(d).reduce((a, v) => a + v.length, 0)} ${t('条')}`;
      if (!document.getElementById('player').classList.contains('open')) render(currentCat);
    }
  } catch (e) {}
}
refreshLive();
setInterval(refreshLive, 10000);

/* 缩略图: 服务端抽帧的静态图, 原生懒加载; 不再用 <video> 预览 (会把设备和解码器一起拖死) */
function reasonOf(ev) {
  return (ev.meta && ev.meta.reason) || 'unknown';
}
/* 事件目录名以 YYYY-MM-DD_HH-MM-SS 开头, 取前 10 位作为日期 */
/* 哨兵事件: reason 以 sentry_ 开头; camera 字段只有哨兵触发才有意义 */
function isSentryEv(md) {
  return String((md || {}).reason || '').indexOf('sentry_') === 0;
}
/* event.json 的 camera 编号 -> 片段文件后缀 (社区公认映射: 0前 3左前 4右前 5左B 6右B 7后) */
const CAM_IDX = {0:'front', 3:'left_pillar', 4:'right_pillar', 5:'left_repeater', 6:'right_repeater', 7:'back'};
/* 片段组 key (YYYY-MM-DD_HH-MM-SS) -> 墙钟毫秒; 车机片段名与 event.json timestamp 同一时基, 直接比较 */
function clipEpoch(k) {
  const m = String(k || '').match(/^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() : 0;
}
/* 哨兵封面: 触发摄像头 + 触发那一帧; 找不到对应片段/摄像头返回 null 回落默认封面 */
function sentryThumbSrc(ev, groups) {
  const md = ev.meta || {};
  const camKey = CAM_IDX[+md.camera];
  const m = String(md.timestamp || '').match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (!camKey || !m) return null;
  const tg = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
  if (!tg) return null;
  for (const g of groups) {                       // groups 已按片段开始时间升序
    const s = clipEpoch(g.key);
    if (!s || tg < s || tg > s + 70e3) continue;  // 触发时刻落在这 1 分钟片段内
    const v = g.vids.find(x => x.cam === camKey);
    if (!v) return null;                          // 触发摄像头该分钟没录到, 回落
    return v.url.replace('/media/', '/thumb/') + '?t=' + Math.max(0, Math.round((tg - s) / 1000));
  }
  return null;
}
function dateOf(ev) {
  const m = String(ev.name || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? m[1] + '-' + m[2] + '-' + m[3] : '';
}
/* 官方App哨兵分类: 警觉 / 车门拉动 / 遭遇袭击 (reason 前缀映射, 未知 sentry_ 值归入警觉) */
function reasonTab(r) {
  if (r.indexOf('sentry_panic') === 0 || r.indexOf('sentry_aware_accel') === 0) return 'panic';
  if (r.indexOf('sentry_aware_door') === 0 || r.indexOf('sentry_aware_handle') === 0) return 'door';
  return 'aware';
}
const SENTRY_TABS = [['all', '全部'], ['aware', '哨兵警觉'], ['door', '车门拉动'], ['panic', '遭遇袭击']];
/* 筛选匹配: 哨兵分类按官方三分类的 key 匹配, 其他分类按原始 reason 精确匹配 */
function matchReason(cat, ev) {
  if (reasonFilter === 'all') return true;
  if (cat === 'SentryClips') return reasonTab(reasonOf(ev)) === reasonFilter;
  return reasonOf(ev) === reasonFilter;
}
/* 两行筛选: 触发原因 / 日期, 互为主次 (计数按对方的筛选结果算) */
function renderFilters(cat) {
  const evs = data[cat] || [];
  const rbox = document.getElementById('filters'), dbox = document.getElementById('dfilters');
  const byDate = dateFilter === 'all' ? evs : evs.filter(ev => dateOf(ev) === dateFilter);
  const byReason = reasonFilter === 'all' ? evs : evs.filter(ev => matchReason(cat, ev));

  // 原因筛选在新分类里可能不存在, 自动回落避免"空列表卡死"; 日期允许选到没有事件的那天(会显示空态提示)
  if (reasonFilter !== 'all' && !evs.some(ev => matchReason(cat, ev))) reasonFilter = 'all';

  if (cat === 'SentryClips') {
    // 官方App样式: 固定分类行常驻, 计数为 0 也显示 (和车机/官方App一致)
    const cnt = { all: byDate.length };
    byDate.forEach(ev => { const t = reasonTab(reasonOf(ev)); cnt[t] = (cnt[t] || 0) + 1; });
    rbox.className = 'filters show';
    rbox.innerHTML = SENTRY_TABS.map(([k, label]) =>
      `<button class="chip${k === reasonFilter ? ' on' : ''}" onclick="setReason('${k}')">${t(label)}<span class="n">${cnt[k] || 0}</span></button>`
    ).join('');
  } else {
    const rcnt = {};
    byDate.forEach(ev => { const r = reasonOf(ev); rcnt[r] = (rcnt[r] || 0) + 1; });
    const rkeys = Object.keys(rcnt).sort((a, b) => rcnt[b] - rcnt[a]);
    if (rkeys.length < 2) { rbox.className = 'filters'; rbox.innerHTML = ''; }
    else {
      rbox.className = 'filters show';
      const items = [['all', t('全部'), byDate.length]].concat(
        rkeys.map(k => [k, reasonLabel(k) !== k ? reasonLabel(k) : t('未标注原因'), rcnt[k]]));
      rbox.innerHTML = '<span class="flab">' + t('原因') + '</span>' + items.map(([k, label, n]) =>
        `<button class="chip${k === reasonFilter ? ' on' : ''}" onclick="setReason('${k}')">${label}<span class="n">${n}</span></button>`
      ).join('');
    }
  }

  const dcnt = {};
  byReason.forEach(ev => { const d = dateOf(ev); if (d) dcnt[d] = (dcnt[d] || 0) + 1; });
  if (dateFilter !== 'all' && !/^\d{4}-\d{2}-\d{2}$/.test(dateFilter)) dateFilter = 'all';
  const alldays = [...new Set(evs.map(dateOf).filter(Boolean))].sort().reverse();
  let days = alldays.filter(d => dcnt[d] > 0 || d === dateFilter);
  // 自选日期可能当天没有事件, 仍然把选中的那天显示出来, 方便点回「全部日期」
  if (dateFilter !== 'all' && !days.includes(dateFilter)) days = [dateFilter].concat(days);
  if (!alldays.length && dateFilter === 'all') { dbox.className = 'filters'; dbox.innerHTML = ''; return; }
  dbox.className = 'filters show';
  const items = [['all', t('全部日期'), byReason.length]].concat(
    days.map(d => [d, d.slice(5).replace('-', '/'), dcnt[d] || 0]));
  dbox.innerHTML = '<span class="flab">' + t('日期') + '</span>' + items.map(([k, label, n]) =>
    `<button class="chip d${k === dateFilter ? ' on' : ''}" onclick="setDate('${k}')">${label}<span class="n">${n}</span></button>`
  ).join('') + '<button class="chip pick" onclick="pickDate()" title="' + t('选择日期…') + '">' + t('选择日期…') + '</button>';
}
/* 原生日期选择器: 分类里只有一天时也能翻到别的日期 */
function pickDate() {
  const inp = document.getElementById('dpick');
  if (!inp) return;
  const t = new Date();
  inp.max = t.getFullYear() + '-' + String(t.getMonth() + 1).padStart(2, '0') + '-' +
    String(t.getDate()).padStart(2, '0');
  inp.value = dateFilter !== 'all' ? dateFilter : '';
  try { if (inp.showPicker) { inp.showPicker(); return; } } catch (e) {}
  inp.click();
}
function setReason(k) {
  reasonFilter = k;
  render(currentCat);
}
function setDate(k) {
  dateFilter = k;
  render(currentCat);
}
/* 缩略图失败多半是临时的 (磁盘满/片段刚同步完还没落稳), 自动重试两次再显示占位图 */
function thumbErr(img) {
  const n = +(img.dataset.r || 0);
  if (n < 2) {
    img.dataset.r = n + 1;
    setTimeout(() => { img.src = img.src.split('?')[0] + '?r=' + (n + 1); }, n ? 8000 : 2500);
    return;
  }
  img.parentNode.innerHTML = '<div class="ph">🎞</div>';
}
/* 日期分组小标题: 今天/昨天/M月D日 */
function dheadLabel(d) {
  const now = new Date(), today = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0');
  const y = new Date(now.getTime() - 864e5), yd = y.getFullYear() + '-' + String(y.getMonth() + 1).padStart(2, '0') + '-' + String(y.getDate()).padStart(2, '0');
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return d;
  if (d === today) return t('今天');
  if (d === yd) return t('昨天');
  if (LANG === 'zh') return (+m[2]) + '月' + (+m[3]) + '日';
  try { return new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString(LANG, { month: 'short', day: 'numeric' }); }
  catch (e) { return d.slice(5); }
}
function render(cat) {
  const g = document.getElementById('grid'), e = document.getElementById('empty');
  g.innerHTML = '';
  renderFilters(cat);
  let evs = data[cat] || [];
  evs = evs.filter(ev => matchReason(cat, ev));
  if (dateFilter !== 'all') evs = evs.filter(ev => dateOf(ev) === dateFilter);
  e.style.display = evs.length ? 'none' : 'block';
  if (!evs.length) {
    e.innerHTML = (reasonFilter !== 'all' || dateFilter !== 'all')
      ? '<div class="big">🔍</div><div>' + t('该筛选下暂无事件') + '<br><small style="font-size:12px">' + t('换个日期或点「全部」查看本分类所有事件') + '</small></div>'
      : EMPTY_HTML;
  }
  let prevDay = null;
  for (const ev of evs) {
    const day = dateOf(ev);
    if (day && day !== prevDay) {           // 日期分组小标题 (像官方App的"下午 14:00-17:59 · 3条")
      const n = evs.filter(x => dateOf(x) === day).length;
      const h = document.createElement('div'); h.className = 'dhead';
      h.innerHTML = '<b>' + dheadLabel(day) + '</b><span class="n">' + n + ' ' + t('条') + '</span>';
      g.appendChild(h);
      prevDay = day;
    }
    const card = document.createElement('div'); card.className = 'card';
    const groups = groupVideos(ev.videos);
    const vids = camSort(groups.length ? groups[groups.length - 1].vids : []);
    let th;
    if (vids.length) {
      /* 哨兵事件: 用触发摄像头在触发那一帧当封面; 其他/回落: 前摄像头第1秒 */
      const src = sentryThumbSrc(ev, groups) ||
        (vids.find(v => v.cam === 'front') || vids[0]).url.replace('/media/', '/thumb/');
      th = `<div class="thumb"><img loading="lazy" decoding="async" src="${src}" alt="" onerror="thumbErr(this)"><span class="cnum">${vids.length} ${t('路')}</span></div>`;
    } else {
      th = `<div class="thumb"><div class="ph">🎞</div></div>`;
    }
    const mm = ev.meta || {};
    /* 标题: 触发原因·触发摄像头 (哨兵), 否则分类名 */
    let title = t(CATS_NAME[cat] || cat);
    if (mm.reason) {
      title = reasonLabel(mm.reason) || mm.reason;
      if (isSentryEv(mm) && mm.camera) {
        const ck = CAM_IDX[+mm.camera];
        if (ck) title += ' · ' + camLabel(ck);
      }
    }
    const tm = (ev.name.match(/_(\d{2}-\d{2})/) || [,''])[1].replace('-', ':');
    const tmTxt = tm ? tm : fmtName(ev.name);
    const extra = (mm.city ? ' · ' + mm.city : '') + (mm.street ? ' ' + mm.street : '');
    const dayTxt = dateFilter === 'all' && day ? day.slice(5).replace('-', '/') + ' · ' : '';
    const mb = ev.size / 1048576;
    const szTxt = mb >= 1 ? mb.toFixed(1) + ' MB' : (mb * 1024).toFixed(0) + ' KB';
    card.innerHTML = th + `<div class="meta"><div class="l1"><span class="rt" title="${title}">${title}</span><span class="tm">${tmTxt}${extra}</span></div><div class="l2"><span>${dayTxt}${vids.length} ${t('路画面')}</span><span class="sz">${szTxt}</span></div></div>`;
    card.onclick = () => openPlayer(ev);
    g.appendChild(card);
  }
  applyLang();
}

function layoutPlayer() {
  const vg = document.getElementById('vgrid');
  if (vg.querySelector('.vcell.focused')) return;  // 单路放大时保持满屏
  const n = vg.children.length;
  if (!n) return;
  const d = gridDims(n);
  vg.style.gridTemplateColumns = `repeat(${d[0]},1fr)`;
  // 显式像素行高: 不依赖 CSS aspect-ratio (老版 iOS Safari 不支持时格子会塌陷/视频层溢出遮挡)
  const w = vg.clientWidth || 0;
  if (w > 0) {
    const gap = 3;
    const cw = (w - gap * (d[0] - 1)) / d[0];
    vg.style.gridAutoRows = Math.round(cw * 9 / 16) + 'px';
  }
}
/* 点击任意一路: 单路铺满; 再点一次(或 Esc)恢复多路 */
function focusCell(cell) {
  const vg = document.getElementById('vgrid');
  const cells = [...vg.children];
  const wasFocused = cell.classList.contains('focused');
  cells.forEach(c => { c.classList.remove('focused'); c.style.display = ''; });
  if (wasFocused) { layoutPlayer(); return; }
  cell.classList.add('focused');
  cells.forEach(c => { if (c !== cell) c.style.display = 'none'; });
  vg.style.gridTemplateColumns = '1fr';
  vg.style.gridTemplateRows = '1fr';
}
function openPlayer(ev) {
  cur = ev;
  curClips = groupVideos(ev.videos);
  curClipIx = curClips.length ? curClips.length - 1 : 0;
  renderClip();
}
function renderClip() {
  const ev = cur;
  const clip = curClips[curClipIx] || { key: '', vids: [] };
  const vg = document.getElementById('vgrid'); vg.innerHTML = '';
  camSort(clip.vids).forEach(v => {
    const cell = document.createElement('div'); cell.className = 'vcell';
    cell.dataset.cam = v.cam || '';
    const vid = document.createElement('video');
    vid.src = qUrl(v.url); vid.muted = true; vid.defaultMuted = true; vid.preload = 'auto';
    /* 每格加载状态: 转码/缓冲时显示提示, 避免黑屏让人以为坏了 */
    const cl = document.createElement('div'); cl.className = 'cload';
    cl.innerHTML = '<span class="spin"></span><span>缓冲中…</span>';
    const clDone = () => { cl.style.display = 'none'; };
    vid.addEventListener('loadeddata', clDone);
    vid.addEventListener('playing', clDone);
    vid.addEventListener('error', () => {
      cl.className = 'cload err';
      cl.innerHTML = '<span>' + t('加载失败, 点其他片段再试') + '</span>';
    });
    vid.addEventListener('waiting', () => { cl.style.display = ''; });
    /* 低清晰度首次播放需要后台转码, 缓冲时给出提示 */
    vid.addEventListener('waiting', () => {
      if (curQ !== 'orig') {
        const h = document.getElementById('qhint');
        h.textContent = t('正在缓冲 (首次播放新片段需后台转码十几秒, 转好一次后秒开)…');
        h.style.display = 'block';
      }
    });
    vid.addEventListener('canplay', () => { document.getElementById('qhint').style.display = 'none'; });
    vid.setAttribute('muted', ''); vid.setAttribute('playsinline', '');
    vid.setAttribute('webkit-playsinline', ''); vid.setAttribute('x5-playsinline', '');
    const lb = document.createElement('span'); lb.className = 'clabel'; lb.textContent = camShort(v.cam);
    cell.appendChild(vid); cell.appendChild(cl); cell.appendChild(lb);
    cell.onclick = () => focusCell(cell);
    vg.appendChild(cell);
  });
  document.getElementById('ic-time').textContent = fmtFull(ev.name);
  const pt = document.getElementById('ptitle');
  if (pt) pt.textContent = t(CATS_NAME[currentCat] || currentCat) + ' · ' + fmtFull(ev.name);
  document.getElementById('ic-cat').textContent = t(CATS_NAME[currentCat] || currentCat);
  const md0 = ev.meta || {};
  document.getElementById('ic-big').textContent =
    (md0.reason && (reasonLabel(md0.reason) || md0.reason)) || CATS_NAME[currentCat] || currentCat;
  const mb = (ev.size || 0) / 1048576;
  document.getElementById('ic-sub').innerHTML = clip.vids.length + ' 路画面 · <span id="ic-dur">--:--</span>' + (mb >= 0.05 ? ' · ' + (mb >= 1 ? mb.toFixed(1) + ' MB' : Math.round(mb * 1024) + ' KB') : '');
  const cb = document.getElementById('clipbar');
  if (curClips.length > 1) {
    cb.classList.add('show');
    cb.innerHTML = '<span class="ick" style="min-width:auto;align-self:center">片段</span>' +
      curClips.map((c, i) => {
        const t = (c.key.split('_')[1] || c.key).replace(/-/g, ':');
        return `<button class="${i === curClipIx ? 'on' : ''}" onclick="switchClip(${i})">${t}</button>`;
      }).join('');
  } else {
    cb.classList.remove('show');
    cb.innerHTML = '';
  }
  const md = ev.meta || {};
  let rh = '';
  rh += '<div data-k="time"><span class="ick">时间</span>' + (md.timestamp ? md.timestamp.replace('T', ' ') : fmtFull(ev.name)) + '</div>';
  if (md.city) rh += '<div data-k="city"><span class="ick">城市</span>' + md.city + '</div>';
  if (md.street) rh += '<div data-k="street"><span class="ick">位置</span>' + md.street + '</div>';
  if (md.est_lat && md.est_lon) rh += '<div data-k="coord"><span class="ick">坐标</span>' + md.est_lat + ', ' + md.est_lon + '</div>';
  if (md.reason) rh += '<div><span class="ick">触发原因</span>' + (reasonLabel(md.reason) || md.reason) + '</div>';
  /* camera 字段只有哨兵事件有意义; 手动保存等也会带 camera:"0" */
  if (md.camera && isSentryEv(md)) { const cn = CAM_IDX[+md.camera]; rh += '<div data-k="camera"><span class="ick">触发画面</span>' + (cn ? camLabel(cn) : '摄像头') + ' #' + md.camera + '</div>'; }
  if (md.campaign_id) rh += '<div><span class="ick">活动ID</span>' + md.campaign_id + '</div>';
  rh += '<div><span class="ick">画面</span>' + clip.vids.length + ' 路</div>';
  if (curClips.length > 1) rh += '<div><span class="ick">片段</span>共 ' + curClips.length + ' 段, 当前第 ' + (curClipIx + 1) + ' 段</div>';
  rh += camSort(clip.vids).map(v => '<div><span class="ick">' + camLabel(v.cam) + '</span>' + v.url.split('/').pop() + '</div>').join('');
  if (!md.timestamp) rh += '<div><span class="ick">元数据</span>无 event.json（该事件未写入元数据）</div>';
  const rows = document.getElementById('ic-rows');
  rows.innerHTML = rh;
  rows.classList.remove('open');
  document.getElementById('ic-fold').textContent = '展开详细数据 ⌄';
  layoutPlayer();
  document.getElementById('player').classList.add('open');
  requestAnimationFrame(layoutPlayer);  // 可见后再算一次, 保证拿到真实宽度设置像素行高
  const sm = document.getElementById('startmask');
  if ('ontouchstart' in window) { sm.classList.add('show'); }
  playAll();
  if (syncTimer) clearInterval(syncTimer);
  syncTimer = setInterval(tick, 250);
  const sortedVids = camSort(clip.vids);
  loadTelemetry(sortedVids.length ? sortedVids[0].url : null, md);
}

/* ---------- 行车遥测面板 (SEI, 视频上方) ---------- */
let telT0 = null, TEL_META = {};
function loadTelemetry(url, meta) {
  TEL = null;
  TEL_META = meta || {};
  const p = document.getElementById('tpanel'), card = document.getElementById('infocard');
  p.classList.remove('show', 'plain');
  if (card) card.classList.remove('plain');
  p.classList.add('folded');   // 详细数据默认折叠
  document.getElementById('tp-fold').textContent = '展开详细数据 ⌄';
  document.getElementById('tp-loc').textContent =
    [TEL_META.city, TEL_META.street].filter(Boolean).join(' · ');
  // 触发画面摄像头: 只有哨兵事件显示 (手动保存等也会带无意义的 camera:"0")
  const camTag = document.getElementById('tp-tag');
  if (TEL_META.camera && isSentryEv(TEL_META)) {
    const cn = CAM_IDX[+TEL_META.camera];
    camTag.innerHTML = '触发画面 <b>' + (cn ? camLabel(cn) : '摄像头') + ' #' + TEL_META.camera + '</b>';
    camTag.classList.add('show');
  } else {
    camTag.classList.remove('show');
    camTag.innerHTML = '';
  }
  telT0 = null;
  if (!url) return;
  const m = url.match(/(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})/);
  if (m) telT0 = new Date(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]);
  // 顶部时间: 优先 event.json 的 timestamp, 否则退回片段文件名里的时间
  const ts = String(TEL_META.timestamp || '').replace('T', ' ').replace(/\.\d+/, '').replace(/Z$/, '').slice(0, 19);
  document.getElementById('tp-date').textContent = ts || (telT0 ? fmtTelDate(telT0) : '—');
  const fp = url.replace('/media/', '/archive/');
  fetch('/api/telemetry?path=' + encodeURIComponent(fp))
    .then(r => r.json())
    .then(d => {
      if (!d.ok || !d.frames || !d.frames.length) { showPlainPanel(); return; }
      TEL = d;
      p.classList.add('show');
      renderHud(d.frames[0]);
      // 事件信息已搬到上方遥测面板, 下方卡片去掉重复项
      ['time', 'city', 'street', 'coord', 'camera'].forEach(k => {
        const el = document.querySelector('#ic-rows > div[data-k="' + k + '"]');
        if (el) el.remove();
      });
      document.getElementById('ic-rows').insertAdjacentHTML('beforeend',
        '<div><span class="ick">刹车占比</span>' + d.brake_ratio + '%</div>' +
        '<div><span class="ick">遥测</span>SEI 元数据 · ' + d.count + ' 帧 @ ' + d.fps + 'fps</div>');
    }).catch(() => showPlainPanel());
}
/* 哨兵/停车片段没有 SEI 遥测: 顶部仍然显示 时间 + 城市·位置, 只是不带车速/挡位 */
function showPlainPanel() {
  const p = document.getElementById('tpanel'), card = document.getElementById('infocard');
  p.classList.add('show', 'plain');
  if (card) card.classList.add('plain');
  ['time', 'city', 'street', 'camera'].forEach(k => {
    const el = document.querySelector('#ic-rows > div[data-k="' + k + '"]');
    if (el) el.remove();
  });
  layoutPlayer();
}
function fmtTelDate(dt) {
  const z = n => (n < 10 ? '0' : '') + n;
  return dt.getFullYear() + '/' + z(dt.getMonth()+1) + '/' + z(dt.getDate()) + ' ' +
    z(dt.getHours()) + ':' + z(dt.getMinutes()) + ':' + z(dt.getSeconds());
}
function renderHud(f) {
  // f: 0=t 1=挡位 2=车速km/h 3=油门% 4=方向盘角度 5=刹车 6=转向灯 7=AP 8=lat 9=lon 10=heading 11=Ax 12=Ay 13=Az
  const q = id => document.getElementById(id);
  q('tp-gear').textContent = f[1];
  q('tp-spd').textContent = Math.max(0, Math.round(f[2]));   // 停车时 SEI 可能给微负值
  q('tp-date').textContent = telT0 ? fmtTelDate(new Date(telT0.getTime() + f[0]*1000)) : '—';
  q('tp-thr-n').textContent = f[3] + '%';
  q('tp-brk').classList.toggle('on', !!f[5]);   // 刹车只有布尔量, 不给百分比
  q('tp-bl').classList.toggle('on', f[6] === 1);
  q('tp-br').classList.toggle('on', f[6] === 2);
  const apNames = { 1: 'FSD', 2: 'AP', 3: 'TACC' };
  const ap = q('tp-ap'), apv = f[7] | 0;
  // 图标常显: 启动时橙红渐变 (FSD/AP/TACC), 未启动时同一个图标灰掉
  ap.style.display = '';
  ap.textContent = apv ? (apNames[apv] || 'AP') : 'AP';
  ap.classList.toggle('off', !apv);
  ap.title = apv ? ('自动驾驶已启动 · ' + (apNames[apv] || 'AP')) : '自动驾驶未启动';
  q('tp-st').title = '方向盘角度 ' + f[4] + '°';
  const md = TEL_META || {};
  q('tp-rows').innerHTML =
    (md.est_lat && md.est_lon ? '<div><span class="k">坐标</span>' + md.est_lat + ', ' + md.est_lon + '</div>' : '') +
    '<div><span class="k">方向盘角度</span>' + f[4] + '°</div>' +
    '<div><span class="k">横向加速度 Ax</span>' + f[11].toFixed(2) + ' m/s²</div>' +
    '<div><span class="k">前后加速度 Ay</span>' + f[12].toFixed(2) + ' m/s²</div>' +
    '<div><span class="k">垂直加速度 Az</span>' + f[13].toFixed(2) + ' m/s²</div>';
}
function toggleTelRows() {
  const p = document.getElementById('tpanel'), b = document.getElementById('tp-fold');
  p.classList.toggle('folded');
  b.textContent = p.classList.contains('folded') ? '展开详细数据 ⌄' : '收起详细数据 ⌃';
  layoutPlayer();
}

/* ---------- 导出视频水印 (与遥测面板同款) ---------- */
function telFrameAt(t) {
  const fr = TEL.frames;
  let lo = 0, hi = fr.length - 1, ans = fr[0];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (fr[mid][0] <= t) { ans = fr[mid]; lo = mid + 1; } else { hi = mid - 1; }
  }
  return ans;
}
function rrPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
function wmPill(ctx, xRight, yMid, text, bg, fg, s) {
  const fs = Math.round(12 * s);
  ctx.font = '700 ' + fs + 'px system-ui,"Segoe UI",sans-serif';
  const pw = ctx.measureText(text).width + Math.round(18 * s), ph = Math.round(24 * s);
  const x = xRight - pw;
  const sh = ctx.shadowBlur;
  ctx.shadowBlur = 0;               // 胶囊是实心色块, 不需要投影
  ctx.fillStyle = bg;
  rrPath(ctx, x, yMid - ph / 2, pw, ph, ph / 2); ctx.fill();
  ctx.shadowBlur = sh;
  ctx.fillStyle = fg;
  ctx.fillText(text, x + Math.round(9 * s), yMid + Math.round(fs * 0.36));
  return x;
}
function drawWm(ctx, t, W) {
  if (!TEL || !TEL.frames || !TEL.frames.length) return;
  const f = telFrameAt(t);
  const s = W / 1280, pad = Math.round(14 * s);
  const h = Math.round(46 * s), yc = h / 2;   // 单行窄条: 1280 宽时约 46px, 仅占画面高度 ~5%
  ctx.save();
  ctx.textBaseline = 'middle';
  // 极淡底 (15% 不透明): 只做一块轻微压暗, 天空/路面几乎不受影响
  ctx.shadowBlur = 0;
  ctx.fillStyle = 'rgba(13,18,26,.15)';
  ctx.fillRect(0, 0, W, h);
  // 文字加深色投影, 保证在明亮天空上也能看清
  ctx.shadowColor = 'rgba(0,0,0,.8)';
  ctx.shadowBlur = Math.round(5 * s);
  ctx.shadowOffsetY = Math.round(1.2 * s);
  // 左: 日期时间 + 城市/位置
  ctx.textAlign = 'left';
  ctx.font = '600 ' + Math.round(14 * s) + 'px system-ui,"Segoe UI",sans-serif';
  ctx.fillStyle = '#fff';
  const dateStr = telT0 ? fmtTelDate(new Date(telT0.getTime() + f[0] * 1000)) : '';
  ctx.fillText(dateStr, pad, yc + Math.round(1 * s));
  const dw = ctx.measureText(dateStr).width;
  const loc = [(TEL_META || {}).city, (TEL_META || {}).street].filter(Boolean).join(' · ');
  if (loc) {
    ctx.font = '500 ' + Math.round(13 * s) + 'px system-ui,"Segoe UI",sans-serif';
    ctx.fillStyle = '#eef3f8';
    ctx.fillText(loc, pad + dw + Math.round(10 * s), yc + Math.round(1 * s));
  }
  // 右侧自右向左: 油门 / 刹车 / 挡位 / 转向灯 / 车速
  let xr = W - pad;
  xr = wmPill(ctx, xr, yc, f[3] + '%', '#1f7ae0', '#fff', s) - Math.round(8 * s);
  xr = wmPill(ctx, xr, yc, '刹车',
    f[5] ? '#ff453a' : 'rgba(32,40,50,.5)', f[5] ? '#fff' : '#eef3f8', s) - Math.round(12 * s);
  // 挡位圆徽
  ctx.shadowBlur = 0;

  const gr = Math.round(13 * s);
  ctx.fillStyle = '#34c759';
  ctx.beginPath(); ctx.arc(xr - gr, yc, gr, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = '#fff'; ctx.textAlign = 'center';
  ctx.font = '800 ' + Math.round(14 * s) + 'px system-ui,"Segoe UI",sans-serif';
  ctx.fillText(f[1], xr - gr, yc + Math.round(1 * s));
  xr -= gr * 2 + Math.round(10 * s);
  // 转向灯三角 (右/左)
  const triS = Math.round(9 * s);
  ctx.fillStyle = f[6] === 2 ? '#f59e0b' : '#4a545f';
  ctx.beginPath(); ctx.moveTo(xr - triS, yc - triS * 0.62); ctx.lineTo(xr - triS, yc + triS * 0.62); ctx.lineTo(xr, yc); ctx.closePath(); ctx.fill();
  xr -= triS + Math.round(7 * s);
  ctx.fillStyle = f[6] === 1 ? '#f59e0b' : '#4a545f';
  ctx.beginPath(); ctx.moveTo(xr, yc - triS * 0.62); ctx.lineTo(xr, yc + triS * 0.62); ctx.lineTo(xr - triS, yc); ctx.closePath(); ctx.fill();
  xr -= triS + Math.round(12 * s);
  // 车速 (先画右侧单位, 再画数字, 保证 "数字 km/h" 顺序)
  ctx.shadowBlur = Math.round(4 * s);   // 文字恢复投影
  ctx.textAlign = 'right';
  ctx.font = '500 ' + Math.round(11 * s) + 'px system-ui,"Segoe UI",sans-serif';
  ctx.fillStyle = '#eef3f8';
  ctx.fillText('km/h', xr, yc + Math.round(1 * s));
  xr -= ctx.measureText('km/h').width + Math.round(6 * s);
  ctx.font = '800 ' + Math.round(22 * s) + 'px system-ui,"Segoe UI",sans-serif';
  ctx.fillStyle = '#fff';
  const sp = String(Math.max(0, Math.round(f[2])));
  ctx.fillText(sp, xr, yc + Math.round(1 * s));
  // AP 方向盘徽标: 开了辅助驾驶 = 橙红实心圆 + 白色方向盘, 没开 = 深灰半透明圆 + 灰色方向盘
  // (以前没开就直接不画, 看起来像"没有这个图标"; 现在常显, 只区分状态)
  xr -= ctx.measureText(sp).width + Math.round(9 * s);
  const apv = f[7] | 0;
  wmApWheel(ctx, xr, yc, s, apv !== 0);
  ctx.restore();
}
function wmApWheel(ctx, xRight, yMid, s, on) {
  // 圆形徽标 + 方向盘图形 (与遥测面板里的方向盘图标同款): 圆环 + 横辐 + 竖辐 + 中心毂
  const R = Math.round(11 * s), cx = xRight - R, cy = yMid;
  const sh = ctx.shadowBlur;
  ctx.shadowBlur = 0;                       // 实心色块, 不要投影
  if (on) {
    const g = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
    g.addColorStop(0, '#f59e0b'); g.addColorStop(1, '#ef4444');
    ctx.fillStyle = g;
  } else {
    ctx.fillStyle = 'rgba(38,46,56,.62)';   // 未启动: 中性灰底, 图标可见但明显灰掉
  }
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
  const ir = R * 0.60, lw = Math.max(1.2 * s, R * 0.13);
  const col = on ? '#fff' : '#c3ccd6';
  ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = lw;
  ctx.beginPath(); ctx.arc(cx, cy, ir, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(cx - ir, cy); ctx.lineTo(cx + ir, cy);
  ctx.moveTo(cx, cy); ctx.lineTo(cx, cy + ir);
  ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, R * 0.16, 0, Math.PI * 2); ctx.fill();
  ctx.shadowBlur = sh;
  return cx - R - Math.round(8 * s);
}
function updateHud(t) {
  if (!TEL || !TEL.frames || !TEL.frames.length) return;
  const fr = TEL.frames;
  let lo = 0, hi = fr.length - 1, ans = fr[0];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (fr[mid][0] <= t) { ans = fr[mid]; lo = mid + 1; } else { hi = mid - 1; }
  }
  renderHud(ans);
}
window.addEventListener('resize', () => { if (document.getElementById('player').classList.contains('open')) layoutPlayer(); });

/* ---------- 准实时预览 ---------- */
let liveCam = localStorage.getItem('tsflivecam') || 'front';
let liveFolder = '', liveTimer = null, liveBusy = false, liveLoading = false, liveSyncTimer = null, liveEv = null;
function latestLiveEv() { const arr = (data && data.RecentClips) || []; return arr[0] || null; }
function openLive() {
  document.getElementById('livep').classList.add('open');
  const ev = latestLiveEv();
  if (ev) { liveFolder = ev.name; renderLive(ev); }
  liveTick();
  liveRestart();
}
function closeLive() {
  document.getElementById('livep').classList.remove('open');
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  clearLiveGrid();
  const v = document.getElementById('lv-video');
  try { v.pause(); v.removeAttribute('src'); v.load(); } catch (err) {}
  /* 从实时预览返回时, 高亮恢复到当前分类页签 (不是固定第一个) */
  const tl = document.getElementById('tab-live');
  if (tl && tl.classList.contains('active')) {
    tl.classList.remove('active');
    const all = document.querySelectorAll('#tabs .tab');
    const ix = Object.keys(data || {}).indexOf(currentCat);
    const target = all[ix >= 0 ? ix : 0];
    if (target && target !== tl) target.classList.add('active');
  }
}
function renderLive(ev) {
  liveEv = ev;
  document.getElementById('lv-name').textContent = '最新片段: ' + ev.name.replace(/_/g, ' ');
  const vids = camSort(ev.videos || []);
  if (!vids.length) return;
  if (liveCam !== '__all' && !vids.find(v => v.cam === liveCam)) liveCam = '__all';
  const row = document.getElementById('lv-cams');
  row.innerHTML = '';
  const mkBtn = (cam, label) => {
    const b = document.createElement('button');
    b.className = 'lvbtn' + (cam === liveCam ? ' on' : '');
    b.textContent = label;
    b.onclick = () => { liveCam = cam; localStorage.setItem('tsflivecam', liveCam); renderLive(ev); };
    row.appendChild(b);
  };
  vids.forEach(v => mkBtn(v.cam, camLabel(v.cam)));
  /* 最后一个: 全览 (所有摄像头同屏) */
  mkBtn('__all', '全览 (' + vids.length + ')');
  const vid = document.getElementById('lv-video');
  const grid = document.getElementById('lv-grid');
  if (liveCam === '__all') {
    vid.style.display = 'none';
    try { vid.pause(); vid.removeAttribute('src'); vid.load(); } catch (err) {}
    buildLiveGrid(vids);
  } else {
    clearLiveGrid();
    vid.style.display = '';
    const v = vids.find(x => x.cam === liveCam) || vids[0];
    if (v && vid.src.indexOf(v.url) === -1) { vid.src = v.url; vid.play().catch(() => {}); }
  }
}
/* 全览: 每路一个 video, 同屏播放并互相对齐进度 */
function clearLiveGrid() {
  if (liveSyncTimer) { clearInterval(liveSyncTimer); liveSyncTimer = null; }
  const g = document.getElementById('lv-grid');
  g.classList.remove('on', 'n1');
  [...g.querySelectorAll('video')].forEach(v => { try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) {} });
  g.innerHTML = '';
}
function buildLiveGrid(vids) {
  const g = document.getElementById('lv-grid');
  [...g.querySelectorAll('video')].forEach(v => { try { v.pause(); v.removeAttribute('src'); } catch (e) {} });
  g.innerHTML = '';
  g.classList.add('on');
  g.classList.toggle('n1', vids.length === 1);
  vids.forEach((v, ix) => {
    const cell = document.createElement('div');
    cell.className = 'lvcell';
    const el = document.createElement('video');
    el.playsInline = true; el.muted = true; el.preload = 'auto';
    el.onloadedmetadata = () => { el.currentTime = 0.05; el.play().catch(() => {}); };
    const lb = document.createElement('div');
    lb.className = 'clabel';
    lb.textContent = camLabel(v.cam);
    cell.appendChild(el); cell.appendChild(lb);
    cell.onclick = () => { liveCam = v.cam; localStorage.setItem('tsflivecam', liveCam); if (liveEv) renderLive(liveEv); };
    g.appendChild(cell);
    /* 逐路上屏: 每路错开 350ms, 避免 4 路同时抢占上行 */
    el.oncanplay = null;
    setTimeout(() => { if (!el.getAttribute('src')) el.src = v.url; }, ix * 350);
  });
  /* 每秒对齐一次进度, 任一路拖进度条/暂停 → 其余跟随 */
  if (liveSyncTimer) clearInterval(liveSyncTimer);
  liveSyncTimer = setInterval(() => {
    const vs = [...g.querySelectorAll('video')];
    if (!vs.length) return;
    const lead = vs.find(v => !v.paused && !v.seeking) || vs[0];
    vs.forEach(v => {
      if (v === lead) return;
      if (Math.abs((v.currentTime || 0) - (lead.currentTime || 0)) > 0.45) v.currentTime = lead.currentTime;
    });
  }, 1000);
}
async function liveTick() {
  if (liveBusy || !document.getElementById('livep').classList.contains('open')) return;
  liveBusy = true;
  const st = document.getElementById('lv-status');
  if (!st.textContent) st.textContent = '正在检查…';   /* 首次进入先给占位, 不让状态栏空白 */
  try {
    /* 网络抖动/上行被视频占满时响应可能为空或迟迟不回, 6 秒超时后放行, 下轮再试 */
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), 6000);
    let txt;
    try { txt = await fetch('/api/live/status', { signal: ctrl.signal }).then(x => x.text()); }
    finally { clearTimeout(to); }
    let r = null;
    try { r = JSON.parse(txt); } catch (e) {}
    if (!r) { st.textContent = '检查暂时不可达, 下轮重试…'; return; }
    /* 状态文字先更新: 下面的全量列表刷新可能很慢 (设备负载高时数秒), 不能让它挡住状态栏 */
    if (!r.folder) st.textContent = '归档里还没有行车片段';
    else {
      /* age = 入库时刻 - 当前时刻 (负数), -1 表示目录里还没有可读文件 */
      const ago = (typeof r.age === 'number' && r.age < -1) ? -r.age : 0;
      st.textContent = !ago ? '' : ago < 60 ? '刚刚入库'
        : ago < 3600 ? Math.round(ago / 60) + ' 分钟前入库'
        : Math.round(ago / 3600) + ' 小时前入库';
    }
    /* 列表刷新异步进行, 不阻塞轮询; 防止上一次还没完成又发起一次 */
    if (r.folder && r.folder !== liveFolder && !liveLoading) {
      liveFolder = r.folder;
      liveLoading = true;
      load().then(() => {
        const ev = latestLiveEv();
        if (ev && document.getElementById('livep').classList.contains('open')) renderLive(ev);
      }).catch(() => {}).finally(() => { liveLoading = false; });
    }
  } catch (e) { st.textContent = '检查暂时不可达, 下轮重试…'; }
  finally { liveBusy = false; }
}
function liveRestart() {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  const iv = +document.getElementById('lv-iv').value;
  if (iv > 0) liveTimer = setInterval(liveTick, iv * 1000);
}

/* ---------- 导出 ---------- */
let expQ = 'orig';
function renderExpQ() {
  const curLabel = (Q_LIST.find(x => x[0] === expQ) || Q_LIST[0])[1];
  document.getElementById('expq').innerHTML =
    `<button class="qbtn ${expQ !== 'orig' ? 'on' : ''}" onclick="toggleExpQ(event)">${curLabel} ⌃</button>` +
    '<div class="qmenu">' + Q_LIST.map(([k, label]) =>
      `<button class="${k === expQ ? 'on' : ''}" onclick="expQ='${k}';renderExpQ();closeQ()">${label}</button>`).join('') + '</div>';
}
function toggleExpQ(e) {
  if (e) e.stopPropagation();
  document.getElementById('expq').classList.toggle('open');
}
function openExport() {
  if (!cur) return;
  const box = document.getElementById('expchks'); box.innerHTML = '';
  camSort(curVids()).forEach(v => {
    const lb = document.createElement('label'); lb.className = 'expitem';
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.value = v.cam; cb.checked = true;
    lb.appendChild(cb);
    lb.appendChild(document.createTextNode(camLabel(v.cam) + ' (' + v.cam + ')'));
    box.appendChild(lb);
  });
  // 水印勾选: 有遥测数据才可用, 默认勾上
  const hasTel = !!(TEL && TEL.frames && TEL.frames.length);
  const wm = document.getElementById('expwm'), wmRow = document.getElementById('expwm-row');
  wm.disabled = !hasTel;
  wm.checked = hasTel;
  wmRow.style.opacity = hasTel ? '' : '.45';
  wmRow.title = hasTel ? '' : '该片段没有行车遥测数据 (SEI)';
  expQ = curQ;  // 默认跟随当前播放清晰度
  renderExpQ();
  document.getElementById('expp').classList.add('open');
}
function closeExport() { document.getElementById('expp').classList.remove('open'); }
function expChk(on) { [...document.querySelectorAll('#expchks input')].forEach(c => c.checked = on); }
function doExport() {
  if (!cur) return;
  const sel = [...document.querySelectorAll('#expchks input:checked')].map(c => c.value);
  if (!sel.length) { toast('请至少选择一路'); return; }
  const telOk = !!(TEL && TEL.frames && TEL.frames.length);
  const wm = document.getElementById('expwm').checked && telOk;
  closeExport();
  if (sel.length === 1 && !wm) {
    const v = curVids().find(x => x.cam === sel[0]);
    if (v) location.href = '/dl?path=' + encodeURIComponent('/archive/' + v.url.replace(/^\/media\//, '')) + (expQ !== 'orig' ? '&q=' + expQ : '');
    return;
  }
  // iOS 上浏览器合成只能出 webm (微信发不出去), 交给设备端 ffmpeg 直接生成 mp4
  if (IS_IOS) { startServerExport(sel, wm); return; }
  startLocalStitch(sel, document.getElementById('stitch'), wm);
}
/* 设备端合成导出: 输出标准 H.264 mp4 (带 faststart), 微信/相册都能直接发 */
function startServerExport(sel, wm) {
  const parts = ((curVids()[0] || {}).url || '').replace(/^\/media\//, '').split('/');
  const cat = parts[0] || '', name = parts[1] || '';
  if (!name) { toast('取不到当前事件信息, 请刷新重试'); return; }
  /* 只导出用户当前正在看的这一分钟, 把真实文件名告诉设备端 */
  const files = curVids()
    .filter(v => sel.indexOf(v.cam) >= 0)
    .map(v => v.f || v.url.split('/').pop())
    .filter(Boolean);
  const st = document.getElementById('stitch');
  const fill = document.getElementById('stfill'), pct = document.getElementById('stpct'),
        hint = document.getElementById('sthint');
  fill.style.width = '0%'; pct.textContent = '0%';
  hint.textContent = '正在设备上合成 mp4 (约 1~3 分钟, 请保持页面打开)…';
  st.classList.add('open');
  fetch('/api/export/burn', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cat: cat, name: name, cams: sel, wm: !!wm, files: files,
                           q: (expQ === '480p' || expQ === '360p') ? '480p' : '720p' })
  }).then(r => r.json()).then(d => {
    if (!d.ok) { stitchFail(d.error || '导出失败'); return; }
    pollExport(d.id);
  }).catch(e => stitchFail('导出失败: ' + e));
}
function pollExport(id) {
  fetch('/api/stitch/status?id=' + encodeURIComponent(id)).then(r => r.json()).then(d => {
    if (d.state === 'error') { stitchFail(d.err || '导出失败'); return; }
    const pct = d.pct || 0;
    document.getElementById('stfill').style.width = pct + '%';
    document.getElementById('stpct').textContent = pct + '%';
    if (d.state === 'done') {
      document.getElementById('sthint').textContent = '合成完成, 开始下载…';
      location.href = '/api/export/burn/dl?id=' + encodeURIComponent(id);
      setTimeout(() => document.getElementById('stitch').classList.remove('open'), 2500);
      return;
    }
    setTimeout(() => pollExport(id), 1000);
  }).catch(() => setTimeout(() => pollExport(id), 1500));
}
function doPushFeishu() {
  if (!cur) return;
  const sel = [...document.querySelectorAll('#expchks input:checked')].map(c => c.value);
  if (!sel.length) { toast('请至少选择一路'); return; }
  const telOk = !!(TEL && TEL.frames && TEL.frames.length);
  const wm = document.getElementById('expwm').checked && telOk;
  closeExport();
  const parts = ((curVids()[0] || {}).url || '').replace(/^\/media\//, '').split('/');
  const cat = parts[0] || '', name = parts[1] || '';
  if (!name) { toast('取不到当前事件信息, 请刷新重试'); return; }
  startLocalStitch(sel, document.getElementById('stitch'), wm, { cat: cat, name: name, q: expQ });
}
function uploadPushVideo(blob, push) {
  const fill = document.getElementById('stfill'), pct = document.getElementById('stpct'),
        hint = document.getElementById('sthint');
  fill.style.width = '60%'; pct.textContent = '';
  hint.textContent = '已上传到设备, 正在转码并推送飞书 (约1-3分钟, 请保持页面打开)…';
  fetch('/api/push/upload?cat=' + encodeURIComponent(push.cat) + '&name=' + encodeURIComponent(push.name) +
        '&q=' + encodeURIComponent(push.q),
        { method: 'POST', body: blob, headers: { 'Content-Type': blob.type || 'video/webm' } })
    .then(r => r.json()).then(d => {
      if (d.ok) {
        fill.style.width = '100%'; pct.textContent = '100%';
        hint.textContent = '✅ ' + d.msg;
        setTimeout(() => document.getElementById('stitch').classList.remove('open'), 6000);
      } else stitchFail(d.msg || d.error || '推送失败');
    })
    .catch(e => stitchFail('推送失败: ' + e));
}
/* 浏览器本机合成: 设备端 ffmpeg 多路 1080p 会触发内核重启, 改由电脑实时录制合成 */
let recBusy = false;
function startLocalStitch(sel, st, wm, push) {
  if (recBusy) { toast('已有合成在进行'); return; }
  const vids = camSort(curVids()).filter(v => sel.includes(v.cam));
  if (!vids.length) { toast('请至少选择一路'); return; }
  if (!window.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) {
    toast('此浏览器不支持本机合成, 请使用打包下载'); return;
  }
  const fill = document.getElementById('stfill'), pct = document.getElementById('stpct'),
        hint = document.getElementById('sthint');
  fill.style.width = '0%'; pct.textContent = '0%';
  hint.textContent = '正在本机合成 ' + vids.length + ' 路画面 (实时录制, 约 1 分钟), 请保持页面前台';
  st.classList.add('open');
  recBusy = true;
  const n = vids.length;
  let cols, CW, CH;
  if (n === 1) { cols = 1; CW = 1280; CH = 960; }        // 单路保持 4:3 原比例
  else { cols = n <= 4 ? 2 : 3; CW = 640; CH = 360; }
  const canvas = document.createElement('canvas');
  canvas.width = cols * CW; canvas.height = Math.ceil(n / cols) * CH;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  const els = vids.map(v => {
    const el = document.createElement('video');
    el.muted = true; el.defaultMuted = true; el.preload = 'auto';
    el.setAttribute('muted', ''); el.setAttribute('playsinline', '');
    el.setAttribute('webkit-playsinline', ''); el.setAttribute('x5-playsinline', '');
    el.src = v.url;
    return el;
  });
  const cleanup = () => {
    els.forEach(e => { try { e.pause(); e.removeAttribute('src'); e.load(); } catch (err) {} });
    recBusy = false;
  };
  Promise.all(els.map(el => new Promise(res => {
    if (el.readyState >= 2) return res();
    el.addEventListener('loadeddata', res, { once: true });
    el.addEventListener('error', () => { toast('有画面加载失败, 请重试'); res(); }, { once: true });
    setTimeout(res, 30000);
  }))).then(() => {
    let mime = '';
    // 优先录 mp4 (微信/相册直接可发), 浏览器不支持再退 webm
    for (const m of ['video/mp4;codecs=avc1.42E01E', 'video/mp4',
                     'video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']) {
      if (MediaRecorder.isTypeSupported(m)) { mime = m; break; }
    }
    if (!mime) { cleanup(); st.classList.remove('open'); toast('此浏览器不支持本机合成'); return; }
    const ext = mime.indexOf('mp4') >= 0 ? 'mp4' : 'webm';
    const dur = Math.max(1, ...els.map(e => isFinite(e.duration) ? e.duration : 0));
    const chunks = [];
    const rec = new MediaRecorder(canvas.captureStream(30), { mimeType: mime, videoBitsPerSecond: 5000000 });
    rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onerror = () => { cleanup(); st.classList.remove('open'); toast('录制失败, 请重试'); };
    rec.onstop = () => {
      cleanup();
      const blob = new Blob(chunks, { type: mime.split(';')[0] });
      if (push) { uploadPushVideo(blob, push); return; }
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = (cur ? cur.name : 'teslacam') + '_' + sel.length + '路.' + ext;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      hint.textContent = t('合成完成') + ', ' + t('已开始下载');
      pct.textContent = '100%'; fill.style.width = '100%';
      setTimeout(() => st.classList.remove('open'), 2500);
    };
    els.forEach(e => { try { e.currentTime = 0; e.play().catch(() => {}); } catch (err) {} });
    rec.start(1000);
    const t0 = performance.now();
    (function draw() {
      const t = (performance.now() - t0) / 1000;
      els.forEach((e, i) => {
        try { ctx.drawImage(e, (i % cols) * CW, Math.floor(i / cols) * CH, CW, CH); } catch (err) {}
      });
      if (wm) drawWm(ctx, t, canvas.width);
      const p = Math.min(99, Math.round(t / dur * 100));
      fill.style.width = p + '%'; pct.textContent = p + '%';
      const allDone = els.every(e => e.ended || (isFinite(e.duration) && e.currentTime >= e.duration - 0.05));
      if (t >= dur + 0.8 || allDone) { try { rec.stop(); } catch (err) {} return; }
      requestAnimationFrame(draw);
    })();
  });
}
function pollStitch(id) {
  fetch('/api/stitch/status?id=' + encodeURIComponent(id)).then(r => r.json()).then(d => {
    if (d.state === 'error') { stitchFail(d.err || t('合成失败')); return; }
    const pct = d.pct || 0;
    document.getElementById('stfill').style.width = pct + '%';
    document.getElementById('stpct').textContent = pct + '%';
    if (d.state === 'done') {
      document.getElementById('sthint').textContent = '合成完成, 开始下载…';
      location.href = '/api/stitch/dl?id=' + encodeURIComponent(id);
      setTimeout(() => document.getElementById('stitch').classList.remove('open'), 2500);
      return;
    }
    setTimeout(() => pollStitch(id), 800);
  }).catch(() => setTimeout(() => pollStitch(id), 1500));
}
function stitchFail(msg) {
  document.getElementById('sthint').textContent = t('合成失败') + ': ' + msg;
  document.getElementById('stpct').textContent = '';
  setTimeout(() => document.getElementById('stitch').classList.remove('open'), 5000);
}
function doExportZip() {
  if (!cur) return;
  const sel = [...document.querySelectorAll('#expchks input:checked')].map(c => c.value);
  if (!sel.length) { toast('请至少选择一路'); return; }
  closeExport();
  location.href = '/api/export/' + encodeURIComponent(currentCat) + '/' + encodeURIComponent(cur.name) + '?cams=' + encodeURIComponent(sel.join(',')) + (expQ !== 'orig' ? '&q=' + expQ : '');
}
document.getElementById('startmask').addEventListener('click', playAll);
function tryPlay(v) { const p = v.play(); if (p && p.catch) p.catch(() => {}); }
function playAll() {
  const vs = [...document.querySelectorAll('#vgrid video')];
  // iOS/微信只允许在用户手势里逐个调 play(); 同步循环内依次触发
  vs.forEach(tryPlay);
  const m = document.getElementById('startmask');
  if (m) m.classList.remove('show');
  const pp = document.getElementById('ppbtn'); if (pp) pp.textContent = '⏸';
}
function pauseAll() {
  document.querySelectorAll('#vgrid video').forEach(v => v.pause());
  const pp = document.getElementById('ppbtn'); if (pp) pp.textContent = '▶';
}
let baseRate = 1;
function setSpeed(s) {
  const r = parseFloat(s);
  baseRate = r;
  document.querySelectorAll('#vgrid video').forEach(v => v.playbackRate = r);
  const b = document.getElementById('spdbtn'); if (b) b.textContent = r + 'x';
}
const SPEEDS = [0.5, 1, 2, 4];
function cycleSpeed() {
  const now = parseFloat(document.getElementById('spdbtn').textContent) || 1;
  setSpeed(SPEEDS[(SPEEDS.indexOf(now) + 1) % SPEEDS.length]);
}
function togglePlay() {
  const vs = [...document.querySelectorAll('#vgrid video')];
  if (!vs.length) return;
  if (vs.some(v => v.paused)) playAll(); else pauseAll();
}
function tick() {
  const vs = [...document.querySelectorAll('#vgrid video')];
  if (!vs.length || !vs[0].duration) return;
  const t = vs[0].currentTime;
  vs.forEach((v, i) => {
    if (i && v.paused && !vs[0].paused && !v.ended) {
      if (v.readyState >= 2) tryPlay(v);   // 还在缓冲(waiting)的视频不要反复强拉, 会卡
      return;
    }
    if (vs[0].paused || v.paused || v.ended) return;
    const d = v.currentTime - t;
    if (Math.abs(d) > 1.2) { v.currentTime = t; }              // 大漂移才硬seek
    else if (Math.abs(d) > 0.05) {                             // 小漂移: 微调倍速平滑收敛, 不跳帧不抽搐
      const m = Math.min(1.12, Math.max(0.88, 1 - d / 2));
      v.playbackRate = baseRate * m;
    } else if (v.playbackRate !== baseRate) {
      v.playbackRate = baseRate;
    }
  });
  document.getElementById('seek').value = Math.round(t / vs[0].duration * 1000);
  document.getElementById('ptime').textContent = fmtSec(t);
  document.getElementById('ic-dur').textContent = fmtSec(vs[0].duration);
  updateHud(t);
}
function fmtSec(x) {
  x = Math.max(0, Math.floor(x || 0));
  return String(Math.floor(x / 60)).padStart(2, '0') + ':' + String(x % 60).padStart(2, '0');
}
let scrubT = 0;
function applySeekAll(frac) {
  const vs = [...document.querySelectorAll('#vgrid video')];
  if (!vs.length || !vs[0].duration) return;
  const tt = frac / 1000 * vs[0].duration;
  vs.forEach(v => { try { v.currentTime = tt; } catch (e) {} });
}
document.getElementById('seek').oninput = function () {
  const now = Date.now();
  if (now - scrubT < 250) return;   // 拖动时节流, 避免 4-6 路同时连发 seek 卡顿
  scrubT = now;
  applySeekAll(+this.value);
};
document.getElementById('seek').onchange = function () {
  scrubT = 0;
  applySeekAll(+this.value);   // 松手必须对齐全部, 否则节流会吞掉最后位置
};
function closePlayer() {
  pauseAll();
  if (syncTimer) clearInterval(syncTimer);
  [...document.querySelectorAll('#vgrid .vcell')].forEach(c => { c.classList.remove('focused'); c.style.display = ''; });
  document.getElementById('player').classList.remove('open');
}
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const f = document.querySelector('#vgrid .vcell.focused');
  if (f) { focusCell(f); return; }
  closeExport(); closePlayer(); closePanel();
});

/* ---------- 设置面板 ---------- */
function toast(msg) {
  const box = document.getElementById('toast');
  box.textContent = t(msg); box.style.display = 'block';
  setTimeout(() => box.style.display = 'none', 2500);
}
function doLogout() {
  if (!confirm(t('确定要退出登录吗？'))) return;
  location.href = '/logout';
}
function stab(which) {
  ['files', 'audio', 'wraps', 'notify', 'auth', 'lang', 'skin', 'update'].forEach(k => {
    const b = document.getElementById('st-' + k), t = document.getElementById('tab-' + k);
    if (b) b.classList.toggle('active', k === which);
    if (t) t.style.display = k === which ? '' : 'none';
  });
  if (which === 'update') { otaCheck(false); autoLoad(); }
  if (which === 'notify') ntLoad();
  if (which === 'auth') pwLoad();
  if (which === 'wraps') refreshWraps();
}

/* ---------- 飞书推送 ---------- */
const NT_SENS = ['nt-to', 'nt-appid', 'nt-hook', 'nt-url'];
let ntHidden = true;            // 默认隐藏, 截图安全
function ntMask(v) {
  v = (v || '').trim();
  if (!v) return '';
  if (v.length <= 8) return '•'.repeat(10);
  return v.slice(0, 4) + '•'.repeat(Math.min(20, v.length - 7)) + v.slice(-3);
}
function ntMaskUrl(v) {
  v = (v || '').trim();
  if (!v) return '';
  const m = /^([a-z]+:\/\/)/i.exec(v);
  return (m ? m[1] : '') + '•'.repeat(12);
}
function ntClearReal() {
  NT_SENS.forEach(id => { const el = document.getElementById(id); if (el) delete el.dataset.real; });
}
function ntReal(id) {
  const el = document.getElementById(id);
  if (!el) return '';
  return (el.dataset.real !== undefined ? el.dataset.real : el.value || '').trim();
}
function ntSetEye(hide) {
  ntHidden = !!hide;
  NT_SENS.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    if (ntHidden) {
      if (el.dataset.real === undefined) el.dataset.real = el.value || '';
      el.value = id === 'nt-url' ? ntMaskUrl(el.dataset.real) : ntMask(el.dataset.real);
      el.readOnly = true;
      el.classList.add('masked');
    } else {
      if (el.dataset.real !== undefined) { el.value = el.dataset.real; delete el.dataset.real; }
      el.readOnly = false;
      el.classList.remove('masked');
    }
  });
  const sec = document.getElementById('nt-secret');
  if (sec) sec.placeholder = ntHidden ? 'App Secret ••••••••••'
    : (sec.dataset.has === '1' ? 'App Secret 已保存, 留空表示不修改' : 'App Secret');
  const btn = document.getElementById('nt-eye'), tip = document.getElementById('nt-eyetip');
  if (btn) btn.textContent = ntHidden ? '👁 显示' : '🙈 隐藏';
  if (tip) tip.textContent = ntHidden ? '敏感信息已隐藏（截图安全）' : '敏感信息已明文显示，注意别外发';
}
function ntToggleEye() { ntSetEye(!ntHidden); }

async function ntLoad() {
  try {
    const j = await (await fetch('/api/notify', { cache: 'no-store' })).json();
    document.getElementById('nt-en').checked = !!j.enabled;
    document.getElementById('nt-saved').checked = !!j.saved;
    document.getElementById('nt-mode').value = j.mode || 'dm';
    document.getElementById('nt-media').value = j.media || 'thumb';
    document.getElementById('nt-quality').value = j.quality || '480p';
    document.getElementById('nt-vdur').value = j.vdur || 30;
    ntMediaUI();
    ntClearReal();
    document.getElementById('nt-to').value = j.receive_id || '';
    document.getElementById('nt-hook').value = j.webhook || '';
    document.getElementById('nt-appid').value = j.app_id || '';
    document.getElementById('nt-secret').value = '';
    document.getElementById('nt-secret').dataset.has = j.has_secret ? '1' : '0';
    document.getElementById('nt-url').value = j.public_url || '';
    document.getElementById('nt-delay').value = j.delay || 20;
    ntSetEye(ntHidden);
    const m = document.getElementById('nt-msg');
    if (j.last && j.last.msg) {
      m.textContent = t('最近一次') + ': ' + (j.last.ok ? '✅ ' : '❌ ') + j.last.msg + ' (' + new Date(j.last.at * 1000).toLocaleString() + ')';
      m.style.color = j.last.ok ? '#4cc38a' : 'var(--accent)';
    } else if (!j.enabled) { m.textContent = t('未启用'); m.style.color = ''; }
    else if (j.dm_ready) { m.textContent = t('已启用私聊推送, 等下一个哨兵事件'); m.style.color = ''; }
    else { m.textContent = t('已启用, 但接收人/应用凭据未配全'); m.style.color = 'var(--accent)'; }
  } catch (e) { document.getElementById('nt-msg').textContent = t('读取设置失败') + ': ' + e; }
}
async function ntSave() {
  const body = {
    enabled: document.getElementById('nt-en').checked,
    saved: document.getElementById('nt-saved').checked,
    mode: document.getElementById('nt-mode').value,
    media: document.getElementById('nt-media').value,
    quality: document.getElementById('nt-quality').value,
    vdur: +document.getElementById('nt-vdur').value || 30,
    receive_id: ntReal('nt-to'),
    webhook: ntReal('nt-hook'),
    app_id: ntReal('nt-appid'),
    app_secret: document.getElementById('nt-secret').value.trim(),
    public_url: ntReal('nt-url'),
    delay: +document.getElementById('nt-delay').value || 20,
  };
  if (body.enabled) {
    const secSaved = document.getElementById('nt-secret').dataset.has === '1';
    const hasApp = body.app_id && (body.app_secret || secSaved);
    if (body.mode === 'dm' && (!hasApp || !body.receive_id)) { toast('私聊需要 App ID / Secret + 接收人'); return; }
    if (body.mode === 'group' && !body.webhook) { toast('请先填群机器人 webhook'); return; }
  }
  const m = document.getElementById('nt-msg');
  m.textContent = t('保存中…');
  try {
    const j = await (await fetch('/api/notify/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
    m.textContent = (j.ok ? '✅ ' + t('已保存') : '❌ ' + j.msg) + (j.warn ? ' · ' + j.warn : '');
    m.style.color = j.ok ? '#4cc38a' : 'var(--accent)';
    if (j.ok) ntLoad();
  } catch (e) { m.textContent = t('保存失败') + ': ' + e; m.style.color = 'var(--accent)'; }
}
async function ntTest() {
  const m = document.getElementById('nt-msg');
  m.textContent = t('正在发送测试卡片…'); m.style.color = '';
  try {
    const j = await (await fetch('/api/notify/test', { method: 'POST' })).json();
    m.textContent = (j.ok ? '✅ ' : '❌ ') + j.msg;
    m.style.color = j.ok ? '#4cc38a' : 'var(--accent)';
  } catch (e) { m.textContent = t('发送失败') + ': ' + e; m.style.color = 'var(--accent)'; }
}

function ntMediaUI() {
  const v = document.getElementById('nt-media').value === 'video';
  document.getElementById('nt-vwrap').style.display = v ? '' : 'none';
}

/* ---------- 账号: 改登录密码 ---------- */
async function pwLoad() {
  try {
    const j = await (await fetch('/api/auth/info', { cache: 'no-store' })).json();
    document.getElementById('pw-user').textContent = j.user || 'admin';
    const m = document.getElementById('pw-msg');
    if (!j.on) { m.textContent = t('当前未开启登录保护, 设置密码后即开启'); m.style.color = 'var(--muted2)'; }
  } catch (e) {}
}
async function pwSave() {
  const m = document.getElementById('pw-msg');
  const o = document.getElementById('pw-old').value;
  const n = document.getElementById('pw-new').value;
  const n2 = document.getElementById('pw-new2').value;
  if (n.length < 6) { m.textContent = t('新密码至少 6 位'); m.style.color = 'var(--accent)'; return; }
  if (n !== n2) { m.textContent = t('两次输入的新密码不一致'); m.style.color = 'var(--accent)'; return; }
  m.textContent = t('提交中…'); m.style.color = '';
  try {
    const j = await (await fetch('/api/auth/pass', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old: o, 'new': n }) })).json();
    if (!j.ok) { m.textContent = '❌ ' + t(j.msg); m.style.color = 'var(--accent)'; return; }
    // 旧 cookie 已随 secret 更换失效, 用新密码立即重新登录, 保持本机不掉线
    try {
      await fetch('/login', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: 'u=' + encodeURIComponent(j.user || 'admin') + '&p=' + encodeURIComponent(n) });
    } catch (e) {}
    document.getElementById('pw-old').value = '';
    document.getElementById('pw-new').value = '';
    document.getElementById('pw-new2').value = '';
    m.textContent = '✅ ' + j.msg;
    m.style.color = '#4cc38a';
  } catch (e) { m.textContent = t('修改失败') + ': ' + e; m.style.color = 'var(--accent)'; }
}

/* ---------- 系统更新 (OTA) ---------- */
let otaInfo = null;
function esc(s) { return String(s == null ? '' : s).replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])); }
async function otaCheck(force) {
  const tag = document.getElementById('upd-tag'), msg = document.getElementById('upd-msg');
  tag.className = 'updtag'; tag.textContent = t('检查中…');
  msg.textContent = t('正在连接 GitHub…');
  try {
    const j = await (await fetch('/api/ota/check' + (force ? '?force=1' : ''), { cache: 'no-store' })).json();
    otaInfo = j;
    document.getElementById('upd-cur').textContent = 'v' + j.current;
    document.getElementById('upd-new').textContent = j.latest ? 'v' + j.latest : '—';
    tag.className = 'updtag' + (j.has_update ? ' new' : '');
    tag.textContent = j.error ? t('检查失败') : (j.has_update ? t('有新版本') : t('已是最新'));
    const n = document.getElementById('upd-notes');
    if (j.notes && j.notes.length) {
      n.innerHTML = j.notes.map(x => '· ' + esc(x)).join('<br>');
      n.style.display = 'block';
    } else { n.style.display = 'none'; n.innerHTML = ''; }
    document.getElementById('upd-go').disabled = !j.has_update;
    document.getElementById('upd-rb').style.display = j.rollback_ready ? '' : 'none';
    renderAutoUpdate(j);
    msg.textContent = j.error
      ? t('连不上 GitHub') + ': ' + j.error + ' ' + t('(可稍后重试)')
      : (t('来源') + ': ' + (j.source || '-') + (j.date ? ' · ' + j.date : '') + (j.has_update ? '' : ' · ' + t('已是最新')));
    document.getElementById('gear').classList.toggle('hasupd', !!j.has_update);
  } catch (e) {
    tag.className = 'updtag'; tag.textContent = t('检查失败');
    msg.textContent = t('检查失败') + ': ' + e;
  }
}
/* 自动更新开关: 勾选即写入 settings.json (默认开启) */
function renderAutoUpdate(j) {
  const cb = document.getElementById('upd-auto'), el = document.getElementById('upd-autohint');
  if (!cb || !el) return;
  cb.checked = !!j.auto;
  let s = j.auto
    ? '已开启: 每 5 分钟检查一次, 发现新版本且没人看视频时自动升级'
    : '已关闭: 只用下方按钮手动升级';
  if (j.auto) {
    const when = j.auto_checked ? new Date(j.auto_checked * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
    if (when || j.auto_msg) s += '<br>上次 ' + (when ? when + ' · ' : '') + (j.auto_msg || '');
  }
  el.innerHTML = s;
}
/* 打开"更新"页时同步一次自动更新开关的真实状态 (不依赖检查结果) */
async function autoLoad() {
  try {
    const s = await (await fetch('/api/settings', { cache: 'no-store' })).json();
    const cb = document.getElementById('upd-auto'), el = document.getElementById('upd-autohint');
    if (!cb || !el) return;
    cb.checked = !!s.auto_update;
    let txt = s.auto_update
      ? '已开启: 每 5 分钟检查一次, 发现新版本且没人看视频时自动升级'
      : '已关闭: 只用下方按钮手动升级';
    const when = s.auto_checked ? new Date(s.auto_checked * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
    if (s.auto_update && (when || s.auto_msg)) txt += '<br>上次 ' + (when ? when + ' · ' : '') + (s.auto_msg || '');
    el.innerHTML = txt;
  } catch (e) {}
}
async function setAutoUpdate(on) {
  const cb = document.getElementById('upd-auto'), el = document.getElementById('upd-autohint');
  try {
    const j = await (await fetch('/api/ota/auto', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on: !!on })
    })).json();
    if (!j.ok) throw new Error(j.msg || '保存失败');
    el.innerHTML = on
      ? '已开启: 每 5 分钟检查一次, 发现新版本且没人看视频时自动升级'
      : '已关闭: 只用下方按钮手动升级';
    if (on) setTimeout(() => otaCheck(false), 800);
  } catch (e) {
    cb.checked = !on;
    toast('❌ 自动更新设置失败: ' + (e.message || e));
  }
}
/* 服务重启期间轮询, 起来后自动刷新页面 */
async function otaWaitBack() {
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 1500));
    try {
      const r = await fetch('/api/live', { cache: 'no-store' });
      if (r.ok) { location.reload(); return; }
    } catch (e) {}
  }
  location.reload();
}
async function otaApply() {
  if (!otaInfo || !otaInfo.has_update) return;
  if (!confirm('升级到 v' + otaInfo.latest + ' ?\n程序文件会被覆盖, 服务自动重启约 5 秒, 期间页面会短暂打不开。')) return;
  const btn = document.getElementById('upd-go'), msg = document.getElementById('upd-msg');
  btn.disabled = true;
  msg.textContent = t('正在下载并校验新版本…');
  try {
    const j = await (await fetch('/api/ota/apply', { method: 'POST' })).json();
    msg.textContent = j.msg;
    if (!j.ok) { btn.disabled = false; return; }
    otaWaitBack();
  } catch (e) {
    msg.textContent = t('升级失败') + ': ' + e;
    btn.disabled = false;
  }
}
async function otaRollback() {
  if (!confirm('回滚到升级前的版本?\n适合新版本出现问题时使用, 服务会自动重启。')) return;
  const msg = document.getElementById('upd-msg');
  msg.textContent = '正在回滚…';
  try {
    const j = await (await fetch('/api/ota/rollback', { method: 'POST' })).json();
    msg.textContent = j.msg;
    if (j.ok) otaWaitBack();
  } catch (e) { msg.textContent = '回滚失败: ' + e; }
}
/* 打开首页后静默检查一次 (结果缓存 10 分钟), 有新版本时齿轮上亮红点 */
setTimeout(() => {
  fetch('/api/ota/check', { cache: 'no-store' }).then(r => r.json()).then(j => {
    if (j && j.has_update) {
      const g = document.getElementById('gear');
      g.classList.add('hasupd');
      g.title = '有新版本 v' + j.latest + ' · 点开设置可升级';
    }
  }).catch(() => {});
}, 5000);
document.getElementById('gear').onclick = () => {
  document.getElementById('mask').classList.add('open');
  document.getElementById('panel').classList.add('open');
  stab('files');
  refreshSkins(); refreshAudio(); refreshFiles();
};
function closePanel() {
  document.getElementById('mask').classList.remove('open');
  document.getElementById('panel').classList.remove('open');
}

async function uploadFile(input, url, fields) {
  const f = input.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  for (const k in (fields || {})) fd.append(k, fields[k]);
  const r = await fetch(url, { method: 'POST', body: fd });
  const j = await r.json();
  toast(j.ok ? '✅ ' + j.msg : '❌ ' + j.msg);
  input.value = '';
  return j;
}

/* 皮肤 */
async function refreshSkins() {
  const j = await (await fetch('/api/skins')).json();
  const list = document.getElementById('skin-list'); list.innerHTML = '';
  if (!j.skins.length) { list.innerHTML = '<div class="hint">暂无皮肤, 使用默认外观</div>'; return; }
  for (const s of j.skins) {
    const d = document.createElement('div'); d.className = 'item';
    d.innerHTML = `<span class="n">${s}</span>` + (s === j.active ? '<span class="tag">使用中</span>' : '') +
      (s === j.active ? '' : `<button class="primary" onclick="applySkin('${s}')">应用</button>`) +
      `<button class="danger" onclick="delSkin('${s}')">删除</button>`;
    list.appendChild(d);
  }
}
async function applySkin(s) {
  await fetch('/api/skin/activate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: s }) });
  toast('皮肤已应用: ' + s); injectSkin(); refreshSkins();
}
async function delSkin(s) {
  if (!confirm('删除皮肤 ' + s + ' ?')) return;
  await fetch('/api/skin/' + encodeURIComponent(s), { method: 'DELETE' });
  toast('已删除'); injectSkin(); refreshSkins();
}
async function injectSkin() {
  let el = document.getElementById('userskin');
  if (!el) { el = document.createElement('style'); el.id = 'userskin'; document.head.appendChild(el); }
  const r = await fetch('/api/skin');
  el.textContent = r.ok ? await r.text() : '';
}
document.getElementById('skin-file').onchange = async function () {
  const j = await uploadFile(this, '/api/upload/skin');
  if (j && j.ok) { refreshSkins(); if (j.activated) { injectSkin(); } }
};

/* 锁车语音 */
async function refreshAudio() {
  const j = await (await fetch('/api/audio')).json();
  const mk = (arr, elid, isDone) => {
    const list = document.getElementById(elid); list.innerHTML = '';
    if (!arr.length) { list.innerHTML = '<div class="hint" style="margin:0">无</div>'; return; }
    for (const f of arr) {
      const d = document.createElement('div'); d.className = 'item';
      const lockTag = f === 'LockChime.wav' ? '<span class="tag">锁车音</span>' : '';
      d.innerHTML = `<span class="n">${f}</span>${lockTag}` +
        (isDone ? '' : `<button class="primary" onclick="setLock('${f}')">设为锁车音</button>`) +
        `<button class="danger" onclick="delAudio('${f}',${isDone})">删除</button>`;
      list.appendChild(d);
    }
  };
  mk(j.queue, 'audio-list', false);
  mk(j.done, 'audio-done', true);
}
async function setLock(f) {
  const r = await fetch('/api/audio/setlockchime', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ name: f }) });
  const j = await r.json();
  toast(j.ok ? '✅ 已设为锁车音 (LockChime.wav)' : '❌ ' + j.msg);
  refreshAudio();
}
async function delAudio(f, isDone) {
  if (!confirm('删除 ' + f + ' ?')) return;
  await fetch('/api/audio/' + encodeURIComponent(f) + '?done=' + (isDone ? 1 : 0), { method: 'DELETE' });
  refreshAudio();
}
document.getElementById('audio-file').onchange = async function () {
  const j = await uploadFile(this, '/api/upload/audio');
  if (j && j.ok) refreshAudio();
};

/* 文件管理器 */
let curDir = '';
let area = 'usb';  // 'usb' = 车机U盘镜像, 'archive' = 归档区
let fmSel = new Set();      // 已勾选的完整路径
let fmPending = null;       // 等待填目标目录的移动/复制任务
let usbAttached = false;    // 车机U盘是否处于网页管理(已挂载)状态
function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}
function fmUpdate() {
  const cnt = document.getElementById('fm-count');
  if (!cnt) return;
  cnt.textContent = '已选 ' + fmSel.size + ' 项';
  const all = document.getElementById('fm-all');
  const boxes = Array.from(document.querySelectorAll('#file-list .fm-chk'));
  const on = boxes.filter(b => b.checked).length;
  all.checked = boxes.length > 0 && on === boxes.length;
  all.indeterminate = on > 0 && on < boxes.length;
}
function fmToggle(p, checked, row) {
  if (checked) fmSel.add(p); else fmSel.delete(p);
  if (row) row.classList.toggle('fm-sel', !!checked);
  fmUpdate();
}
function fmAll(checked) {
  fmSel.clear();
  document.querySelectorAll('#file-list .fm-chk').forEach(b => {
    b.checked = checked;
    const row = b.closest('.item');
    if (row) row.classList.toggle('fm-sel', !!checked);
    if (checked) fmSel.add(b.dataset.p);
  });
  fmUpdate();
}
function fmDestCancel() {
  fmPending = null;
  const box = document.getElementById('fm-dest');
  if (box) box.style.display = 'none';
}
function fmOp(op) {
  if (!fmSel.size) { toast('请先勾选文件或文件夹'); return; }
  const items = Array.from(fmSel);
  if (op === 'rename') {
    if (items.length !== 1) { toast('重命名只能选中一项'); return; }
    const cur = items[0].split('/').pop();
    const nn = prompt('新名称 (可含中文):', cur);
    if (!nn || nn === cur) return;
    fmSend({ area, op, items, name: nn });
    return;
  }
  if (op === 'delete') {
    const preview = items.slice(0, 3).map(x => x.split('/').pop()).join('、');
    if (!confirm('确定删除选中的 ' + items.length + ' 项吗？\n' + preview + (items.length > 3 ? ' …' : '') +
      '\n\n文件夹会连同里面所有内容一起删除，且不可恢复。')) return;
    fmSend({ area, op, items });
    return;
  }
  fmPending = { area, op, items };
  const box = document.getElementById('fm-dest');
  const inp = document.getElementById('fm-dest-input');
  box.style.display = 'flex';
  inp.value = curDir;
  inp.placeholder = area === 'usb' ? '目标目录, 留空=U盘根目录' : '目标目录, 如 /archive/files';
  inp.focus();
}
function fmDestGo() {
  if (!fmPending) return;
  const dest = document.getElementById('fm-dest-input').value.trim();
  if (area === 'usb' && dest.startsWith('/')) { toast('车机U盘请填相对路径 (留空=根目录)'); return; }
  if (area === 'archive' && !dest.startsWith('/archive')) { toast('归档区请填 /archive 开头的绝对路径'); return; }
  const b = fmPending;
  fmDestCancel();
  const verb = b.op === 'move' ? '移动' : '复制';
  if (!confirm('把选中的 ' + b.items.length + ' 项' + verb + '到\n' + (dest || '(U盘根目录)') +
    ' ?\n\n同名会被跳过；复制大视频可能占满U盘空间。')) return;
  fmSend({ area: b.area, op: b.op, items: b.items, dest });
}
async function fmSend(payload) {
  toast('⏳ 正在处理…');
  let j;
  try {
    const r = await fetch('/api/fm', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    j = await r.json();
  } catch (e) { toast('❌ 请求失败: ' + e); return; }
  let msg = (j.ok ? '✅ ' : '❌ ') + (j.msg || '');
  if (j.failed && j.failed.length) msg += ' | ' + j.failed.slice(0, 2).join('; ');
  toast(msg);
  fmSel.clear();
  fmDestCancel();
  refreshFiles();
}
async function purgeTeslaCam() {
  if (area !== 'usb') { toast('请先切换到「车机U盘」'); return; }
  if (!usbAttached) { toast('请先点「连接车机U盘」, 连上后才能清空录像'); return; }
  if (!confirm('⚠️ 将永久删除车机U盘 TeslaCam/ 内的全部录像文件与事件文件夹。\n\n' +
    '• 保留 TeslaCam 及 RecentClips / SavedClips / SentryClips 目录结构\n' +
    '• 不影响 Boombox、Wraps 等其他根目录\n' +
    '• 需先连接 U 盘 (会临时断开车机读取)\n\n确定继续吗？')) return;
  if (!confirm('再次确认：此操作不可撤销，确认清空全部录像？')) return;
  await fmSend({ area: 'usb', op: 'purge_teslacam', items: [] });
}
/* ---------- 涂装 (Wraps) ---------- */
let wrapAttached = false;   // 与文件管理共用同一个 U 盘管理状态
function wrapStatusHtml(attached) {
  if (!attached) {
    return '<div style="background:#3a2a12;border-radius:10px;padding:10px 12px;font-size:12.5px;color:#f0b429;line-height:1.6">' +
      '⚠ U盘当前未连接管理 (车机正常读写中)<br>' +
      '<button onclick="wrapAttach()" style="background:#e8443f;border:none;color:#fff;border-radius:8px;padding:7px 16px;font-size:12.5px;cursor:pointer;margin-top:6px">🔗 连接车机U盘 (临时断开车机读取)</button></div>';
  }
  return '<div style="background:#1a3325;border-radius:10px;padding:10px 12px;font-size:12.5px;color:#4cc38a;line-height:1.6">' +
    '🟢 已连接 — 上传的图片会直接写入车机U盘 Wraps/ 目录<br>' +
    '<span style="color:#7d8894;font-size:11.5px">断开满 5 分钟无操作会自动恢复车机导出</span><br>' +
    '<button onclick="wrapDetach()" style="background:#242c35;border:none;color:#e8eaed;border-radius:8px;padding:7px 16px;font-size:12.5px;cursor:pointer;margin-top:6px">✅ 恢复车机导出</button></div>';
}
async function wrapAttach() {
  toast('⏳ 正在连接 (车机读取将断开几秒)...');
  const j = await (await fetch('/api/usb/attach', { method: 'POST' })).json();
  toast(j.ok ? '✅ 已连接车机U盘' : '❌ ' + j.msg);
  await refreshWraps();
}
async function wrapDetach() {
  const j = await (await fetch('/api/usb/detach', { method: 'POST' })).json().catch(() => ({}));
  toast(j.ok ? '✅ ' + t('已恢复车机导出') : '❌ ' + t(j.msg || '恢复失败'));
  await refreshWraps();
}
async function refreshWraps() {
  const st = document.getElementById('wraps-status');
  const grid = document.getElementById('wraps-grid');
  let j;
  try {
    j = await (await fetch('/api/usb/browse?dir=' + encodeURIComponent('Wraps'))).json();
  } catch (e) { return; }
  wrapAttached = !!j.attached;
  st.innerHTML = wrapStatusHtml(wrapAttached);
  grid.innerHTML = '';
  if (!j.ok) {
    grid.innerHTML = wrapAttached
      ? '<div class="hint">Wraps 目录不存在, 上传图片会自动创建</div>'
      : '<div class="hint">U盘未连接管理 (车机正常读写中), 点上方「连接车机U盘」后可查看 / 上传图片</div>';
    return;
  }
  const files = j.files || [];
  if (!files.length) { grid.innerHTML = '<div class="hint">Wraps 目录为空, 上传图片即可在车机上选用</div>'; return; }
  for (const f of files) {
    const box = document.createElement('div');
    box.style.cssText = 'width:110px;text-align:center';
    const img = document.createElement('img');
    img.src = '/usbdl?path=' + encodeURIComponent('Wraps/' + f.name);
    img.style.cssText = 'width:100px;height:68px;object-fit:contain;background:#0d1117;border-radius:8px;border:1px solid #2a3440;cursor:pointer';
    img.onclick = () => window.open('/usbdl?path=' + encodeURIComponent('Wraps/' + f.name));
    const cap = document.createElement('div');
    cap.textContent = f.name;
    cap.style.cssText = 'font-size:11px;color:#c9d1d9;margin:4px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    const btn = document.createElement('button');
    btn.className = 'danger'; btn.textContent = t('删除');
    btn.style.cssText = 'font-size:11px;padding:4px 12px';
    btn.onclick = async () => {
      if (!confirm(t('删除') + ' Wraps/' + f.name + ' ?')) return;
      const r = await fetch('/api/usb?path=' + encodeURIComponent('Wraps/' + f.name), { method: 'DELETE' });
      const rj = await r.json().catch(() => ({}));
      toast(rj.ok ? '✅ ' + t('已删除') : '❌ ' + (rj.msg || t('删除失败')));
      refreshWraps();
    };
    box.append(img, cap, btn);
    grid.appendChild(box);
  }
}
document.getElementById('wraps-file').onchange = async function () {
  if (!this.files || !this.files.length) return;
  if (!wrapAttached) { toast('请先点「连接车机U盘」, 连上后再上传'); this.value = ''; return; }
  for (const f of this.files) {
    const fd = new FormData(); fd.append('file', f); fd.append('path', 'Wraps');
    try {
      const r = await fetch('/api/upload/usb', { method: 'POST', body: fd });
      const j = await r.json();
      toast((j.ok ? '✅ ' : '❌ ') + f.name + ' — ' + (j.msg || ''));
    } catch (e) { toast('❌ ' + f.name + ' — ' + t('上传失败')); }
  }
  this.value = '';
  await refreshWraps();
};
function switchArea(a) {
  area = a;
  document.getElementById('area-usb').classList.toggle('active', a === 'usb');
  document.getElementById('area-archive').classList.toggle('active', a === 'archive');
  document.getElementById('usb-hint').style.display = a === 'usb' ? '' : 'none';
  document.getElementById('arch-hint').style.display = a === 'archive' ? '' : 'none';
  document.getElementById('purge-box').style.display = a === 'usb' ? 'flex' : 'none';
  curDir = a === 'usb' ? '' : '/archive/files';
  refreshFiles();
}
function usbStatusHtml(attached, msg) {
  if (!attached) {
    return '<div style="background:#3a2a12;border-radius:10px;padding:10px 12px;font-size:12.5px;color:#f0b429;line-height:1.6">' +
      '⚠ U盘当前未连接管理 (车机正常读写中)<br>' +
      '<button onclick="usbAttach()" style="background:#e8443f;border:none;color:#fff;border-radius:8px;padding:7px 16px;font-size:12.5px;cursor:pointer;margin-top:6px">🔗 连接车机U盘 (临时断开车机读取)</button></div>';
  }
  return '<div style="background:#1a3325;border-radius:10px;padding:10px 12px;font-size:12.5px;color:#4cc38a;line-height:1.6">' +
    '🟢 已连接 — U盘已从车机临时断开, 你在网页上的改动会直接写入车机U盘<br>' +
    '<span style="color:#7d8894;font-size:11.5px">断开满 5 分钟无操作会自动恢复车机导出</span><br>' +
    '<button onclick="usbDetach()" style="background:#242c35;border:none;color:#e8eaed;border-radius:8px;padding:7px 16px;font-size:12.5px;cursor:pointer;margin-top:6px">✅ 恢复车机导出</button></div>';
}
async function usbAttach() {
  toast('⏳ 正在连接 (车机读取将断开几秒)...');
  const r = await fetch('/api/usb/attach', { method: 'POST' });
  const j = await r.json();
  toast(j.ok ? '✅ 已连接车机U盘' : '❌ ' + j.msg);
  curDir = '';
  refreshFiles();
}
async function usbDetach() {
  const j = await (await fetch('/api/usb/detach', { method: 'POST' })).json();
  toast(j.ok ? '✅ ' + t('已恢复车机导出') : '❌ ' + t(j.msg));
  document.getElementById('dir-nav').style.display = 'none';
  refreshFiles();
}
function goDir(d) {
  if (area === 'usb') {
    d = (d || '').trim().replace(/^\/+|\/+$/g, '');
    refreshFiles(d);
  } else {
    d = (d || '').trim().replace(/\/+$/, '') || '/archive';
    if (!d.startsWith('/archive')) { toast('❌ 仅支持 /archive 内的路径'); return; }
    refreshFiles(d);
  }
}
async function refreshFiles(dir) {
  if (dir !== undefined) curDir = dir;
  fmSel.clear();
  fmDestCancel();
  const statusEl = document.getElementById('usb-status');
  const nav = document.getElementById('dir-nav');
  const crumb = document.getElementById('crumb');
  const list = document.getElementById('file-list');
  let j;
  if (area === 'usb') {
    j = await (await fetch('/api/usb/browse?dir=' + encodeURIComponent(curDir))).json();
    statusEl.style.display = 'block';
    statusEl.innerHTML = usbStatusHtml(j.attached, j.msg);
    usbAttached = !!j.attached;
    if (!j.attached) { nav.style.display = 'none'; list.innerHTML = ''; fmUpdate(); return; }
    nav.style.display = 'block';
  } else {
    j = await (await fetch('/api/browse?dir=' + encodeURIComponent(curDir))).json();
    statusEl.style.display = 'none';
    if (!j.ok) { toast('❌ ' + (j.msg || '目录不可访问')); return; }
    nav.style.display = 'block';
  }
  curDir = area === 'usb' ? j.dir : j.dir;
  document.getElementById('dest-dir').value = curDir;
  crumb.innerHTML = '';
  const mkLink = (label, target, folder) => {
    const a = document.createElement('a');
    a.textContent = label; a.href = 'javascript:void(0)';
    a.style.cssText = 'color:' + (folder ? '#5ea9ff' : '#7d8894') + ';cursor:pointer;text-decoration:underline';
    a.onclick = () => goDir(target);
    crumb.appendChild(a);
    const s = document.createElement('span'); s.textContent = ' / '; s.style.color = '#3a4652';
    crumb.appendChild(s);
  };
  if (area === 'usb') {
    mkLink('🚗 U盘根目录', '', true);
    let acc = '';
    for (const seg of curDir.split('/').filter(Boolean)) {
      acc = acc ? acc + '/' + seg : seg;
      mkLink(seg, acc, true);
    }
  } else {
    let acc = '';
    for (const seg of curDir.split('/').filter(Boolean)) { acc += '/' + seg; mkLink(seg, acc, true); }
  }
  list.innerHTML = '';
  let empty = true;
  const mkChk = (full, row) => {
    const c = document.createElement('input');
    c.type = 'checkbox'; c.className = 'fm-chk'; c.dataset.p = full;
    c.onchange = () => fmToggle(full, c.checked, row);
    return c;
  };
  for (const d of j.dirs) {
    empty = false;
    const full = area === 'usb' ? (curDir ? curDir + '/' + d : d) : curDir + '/' + d;
    const el = document.createElement('div'); el.className = 'item';
    const n = document.createElement('span'); n.className = 'n';
    n.textContent = '📁 ' + d; n.style.cssText = 'cursor:pointer;color:#5ea9ff';
    n.onclick = () => goDir(full);
    el.append(mkChk(full, el), n);
    list.appendChild(el);
  }
  for (const f of j.files) {
    empty = false;
    const full = area === 'usb' ? (curDir ? curDir + '/' + f.name : f.name) : curDir + '/' + f.name;
    const el = document.createElement('div'); el.className = 'item';
    const n = document.createElement('span'); n.className = 'n'; n.textContent = f.name;
    const tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = fmtSize(f.size || 0);
    const btnDl = document.createElement('button'); btnDl.className = 'primary fm-mini';
    btnDl.textContent = '⬇'; btnDl.title = t('下载');
    btnDl.onclick = async () => {
      const url = (area === 'usb' ? '/usbdl?path=' : '/dl?path=') + encodeURIComponent(full);
      if (area !== 'usb') { location.href = url; return; }   // 归档区可能是大视频, 走浏览器流式下载
      try {   // U盘文件多为小件: fetch 成 blob, 失败只弹提示, 不再整页跳走
        const r = await fetch(url);
        if (!r.ok) { toast('❌ ' + t('文件读不到, U盘可能已自动恢复车机导出, 请重新连接')); return; }
        const b = await r.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(b); a.download = f.name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      } catch (e) { toast('❌ ' + t('下载失败')); }
    };
    const btnDel = document.createElement('button'); btnDel.className = 'danger fm-mini';
    btnDel.textContent = '✕'; btnDel.title = t('删除');
    btnDel.onclick = () => delFile(full);
    el.append(mkChk(full, el), n, tag, btnDl, btnDel);
    list.appendChild(el);
  }
  if (empty) list.innerHTML = '<div class="hint" style="margin:0">' + t('空目录') + '</div>';
  fmUpdate();
}
async function delFile(p) {
  if (!confirm('删除 ' + (area === 'usb' ? 'U盘/' : '') + p + ' ?')) return;
  const url = area === 'usb' ? '/api/usb?path=' : '/api/file?path=';
  await fetch(url + encodeURIComponent(p), { method: 'DELETE' });
  toast('已删除'); refreshFiles();
}
document.getElementById('anyfile-file').onchange = async function () {
  const url = area === 'usb' ? '/api/upload/usb' : '/api/upload/file';
  const j = await uploadFile(this, url, { path: area === 'usb' ? curDir : curDir });
  if (j && j.ok) refreshFiles();
};

injectSkin();
applyLang();
load();
</script></body></html>"""


def ensure_dirs():
    for d in (SKIN_DIR, AUDIO_Q, AUDIO_DONE, FILES_DIR, ARCHIVE):
        os.makedirs(d, exist_ok=True)
    for c in CATS:
        os.makedirs(os.path.join(ARCHIVE, c), exist_ok=True)


def safe_name(name):
    name = os.path.basename(name or "")
    return name if re.match(r'^[\w\-. ]{1,80}$', name) else None


def list_events():
    out = {}
    for cat in CATS:
        base = os.path.join(ARCHIVE, cat)
        events = []
        if os.path.isdir(base):
            for name in sorted(os.listdir(base), reverse=True):
                d = os.path.join(base, name)
                if not os.path.isdir(d):
                    continue
                vids = []
                total = 0
                for f in sorted(os.listdir(d)):
                    if f.lower().endswith(".mp4"):
                        cam = f.rsplit("-", 1)[-1][:-4] if "-" in f else f
                        vids.append({"url": f"/media/{cat}/{name}/{f}", "cam": cam, "f": f})
                        try:
                            total += os.path.getsize(os.path.join(d, f))
                        except OSError:
                            pass
                meta = {}
                ej = os.path.join(d, "event.json")
                if os.path.isfile(ej):
                    try:
                        with open(ej, "r", encoding="utf-8", errors="ignore") as jf:
                            meta = json.load(jf)
                    except Exception:
                        meta = {}
                events.append({"name": name, "videos": vids, "size": total, "meta": meta})
        out[cat] = events
    return out


def _map_archive(p):
    """前端与深链固定用 /archive/... 作为虚拟前缀; 归档区可配置时映射到实际目录"""
    if ARCHIVE != "/archive" and (p == "/archive" or p.startswith("/archive/")):
        return ARCHIVE + p[len("/archive"):]
    return p


def safe_archive_dir(p):
    """校验目录必须落在归档区内 (防路径逃逸), 返回 realpath 或 None"""
    p = _map_archive((p or "").strip())
    if not p.startswith(ARCHIVE):
        return None
    rp = os.path.realpath(p)
    root = os.path.realpath(ARCHIVE)
    if rp != root and not rp.startswith(root + "/"):
        return None
    return rp


def safe_archive_path(p):
    """校验文件路径必须落在归档区内且真实存在"""
    p = _map_archive((p or "").strip())
    if not p.startswith(ARCHIVE):
        return None
    rp = os.path.realpath(p)
    if not rp.startswith(os.path.realpath(ARCHIVE) + "/") or not os.path.isfile(rp):
        return None
    return rp


# ---------- 车机U盘(镜像)临时管理 ----------
IMG = os.environ.get("TSF_IMG", "/root/teslacam.img")
MNT = os.environ.get("TSF_MNT", "/tmp/teslamnt")
LOOPDEV = os.environ.get("TSF_LOOP", "/dev/loop3")
LOSETUP = os.environ.get("TSF_LOSETUP") or (
    "/opt/mu300/busybox-bin/losetup"
    if os.path.exists("/opt/mu300/busybox-bin/losetup")
    else "losetup"  # 通用发行版走 PATH (util-linux)
)
GADGET_DIR = os.environ.get("TSF_GADGET", "/sys/kernel/config/usb_gadget/linux")
USB_AUTO_DETACH = 300  # 秒, 无操作自动恢复导出
# 跨进程 U 盘互斥: 网页挂载(排他) / tesla-sync.sh(排他) / usb-selfheal.sh(排他) /
# tesla-live-sync.py(共享) 共用同一锁文件; 排他锁在整个挂载周期内持有。
# 设备实测 BusyBox flock 与 Python fcntl.flock 可互锁。
USB_LOCK_FILE = os.environ.get("TSF_USB_LOCK", "/tmp/tsf-usb.lock")

usb_lock = threading.Lock()
usb_attached = False
usb_timer = None
_usb_flock_fd = None


def _acquire_usb_flock(exclusive=True):
    """拿跨进程锁, 失败立即返回 False (不排队, 由调用方跳过或重试)"""
    global _usb_flock_fd
    if _usb_flock_fd is not None:
        return True
    fd = None
    try:
        fd = os.open(USB_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return False
    _usb_flock_fd = fd
    return True


def _release_usb_flock():
    global _usb_flock_fd
    fd, _usb_flock_fd = _usb_flock_fd, None
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _udc():
    try:
        return os.listdir("/sys/class/udc")[0]
    except Exception:
        return None


def _gadget_rebind(bind):
    udc = _udc()
    if not udc:
        return False
    try:
        with open(os.path.join(GADGET_DIR, "UDC"), "w") as f:
            f.write(udc if bind else "")
        return True
    except Exception:
        return False


def usb_attach():
    """临时断开车机读取, 挂载镜像到 MNT (读写). 返回 (ok, msg)"""
    global usb_attached, usb_timer
    with usb_lock:
        if usb_attached and os.path.ismount(MNT):
            return True, "already"
        # 先拿跨进程锁再动 UDC: 归档/自愈正在操作镜像时直接让开, 不抢 loop 与挂载点。
        # 在线同步只短暂持共享锁, 等它放开即可, 避免用户偶发"点不动 U盘管理"。
        for _ in range(6):
            if _acquire_usb_flock():
                break
            time.sleep(0.4)
        else:
            return False, "U盘正被其它任务占用 (归档/自愈/同步), 稍后再试"
        try:
            os.makedirs(MNT, exist_ok=True)
        except OSError as ex:
            _release_usb_flock()
            return False, "创建挂载点失败: " + str(ex)
        _gadget_rebind(False)  # 停止导出
        time.sleep(1)
        r = subprocess.run([LOSETUP, LOOPDEV, IMG], capture_output=True, text=True)
        if r.returncode != 0:
            _gadget_rebind(True)
            _release_usb_flock()
            return False, "losetup 失败: " + r.stderr.strip()[-120:]
        r = subprocess.run(["mount", "-t", "exfat", LOOPDEV, MNT], capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run([LOSETUP, "-d", LOOPDEV], capture_output=True)
            _gadget_rebind(True)
            _release_usb_flock()
            return False, "挂载失败: " + r.stderr.strip()[-120:]
        usb_attached = True
        # 无操作自动恢复
        if usb_timer:
            usb_timer.cancel()
        usb_timer = threading.Timer(USB_AUTO_DETACH, usb_detach_safe)
        usb_timer.daemon = True
        usb_timer.start()
        return True, "attached"


def usb_detach():
    """卸载镜像并恢复车机导出. 返回 (ok, msg)"""
    global usb_attached, usb_timer
    with usb_lock:
        if usb_timer:
            usb_timer.cancel()
            usb_timer = None
        if os.path.ismount(MNT):
            subprocess.run(["sync"], capture_output=True)
            for _ in range(3):
                r = subprocess.run(["umount", MNT], capture_output=True, text=True)
                if r.returncode == 0:
                    break
                time.sleep(2)
            else:
                return False, "卸载失败, 有进程占用 " + MNT
        subprocess.run([LOSETUP, "-d", LOOPDEV], capture_output=True)
        _gadget_rebind(True)
        usb_attached = False
        _release_usb_flock()   # 挂载周期结束才释放, 失败路径继续持锁
        return True, "detached"


def usb_detach_safe():
    try:
        ok, msg = usb_detach()
        print("auto detach:", ok, msg)
    except Exception as e:
        print("auto detach error:", e)


def usb_img_age():
    """车机最后一次写U盘镜像距今的秒数; 读不到返回 -1"""
    try:
        return time.time() - os.path.getmtime(IMG)
    except OSError:
        return -1


def live_latest():
    """归档里最新的 RecentClips 片段组与其入库时间 (秒); 没有返回 ("", 0)。
    片段由 tesla-live-sync.py 在后台持续只读同步进归档 (不打断车机), 延时约 1~2 分钟。"""
    base = os.path.join(ARCHIVE, "RecentClips")
    try:
        cands = sorted((n for n in os.listdir(base)
                        if re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", n)
                        and os.path.isdir(os.path.join(base, n))), reverse=True)
    except OSError:
        return "", 0.0
    if not cands:
        return "", 0.0
    newest = 0.0
    try:
        for f in os.listdir(os.path.join(base, cands[0])):
            newest = max(newest, os.path.getmtime(os.path.join(base, cands[0], f)))
    except OSError:
        pass
    return cands[0], newest


def usb_real(p):
    """校验镜像内相对路径, 返回挂载点下真实绝对路径或 None"""
    p = (p or "").strip().strip("/")
    if not p:
        return MNT
    rp = os.path.realpath(os.path.join(MNT, p))
    if rp != MNT and not rp.startswith(MNT + "/"):
        return None
    return rp


# ---------- 文件管理器: 批量操作 (多选删除/移动/复制/重命名) ----------
# 清空 TeslaCam 时保留这几个直属目录, 车机继续录制时会直接往里写
FM_KEEP_ROOTS = ("RecentClips", "SavedClips", "SentryClips")


def _fm_name(s):
    """重命名用的安全文件名: 允许中文, 禁止路径分隔符与控制字符"""
    s = (s or "").strip()
    if not s or len(s) > 120 or s in (".", ".."):
        return None
    if "/" in s or "\\" in s or "\x00" in s or any(ord(c) < 32 for c in s):
        return None
    return s


def _fm_root(area):
    """返回该区域根目录的真实绝对路径; 区域不可用返回 None"""
    if area == "usb":
        if not usb_attached or not os.path.ismount(MNT):
            return None
        return os.path.realpath(MNT)
    if area == "archive":
        return os.path.realpath(ARCHIVE)
    return None


def _fm_resolve(area, p, root, must_exist=True):
    """把前端传来的路径解析为 root 内的真实绝对路径; 越界/根目录/不存在返回 None"""
    if area == "usb":
        rp = usb_real((p or "").strip().strip("/"))
        rp = os.path.realpath(rp) if rp else None
    else:
        q = _map_archive((p or "").strip())
        if not q.startswith(ARCHIVE):
            return None
        rp = os.path.realpath(q)
    if not rp or rp == root or not rp.startswith(root + "/"):
        return None
    if must_exist and not os.path.lexists(rp):
        return None
    return rp


def _fm_dest_dir(area, p, root):
    """移动/复制的目标目录, 允许根目录本身; 不存在或越界返回 None"""
    if area == "usb":
        rp = usb_real((p or "").strip().strip("/"))
        rp = os.path.realpath(rp) if rp else None
    else:
        q = _map_archive((p or "").strip())
        if not q.startswith(ARCHIVE):
            return None
        rp = os.path.realpath(q)
    if not rp or (rp != root and not rp.startswith(root + "/")) or not os.path.isdir(rp):
        return None
    return rp


def _fm_purge_teslacam(root):
    """清空 TeslaCam 内全部录像, 保留 TeslaCam 与三个标准直属目录, 不动其他根目录"""
    base = usb_real("TeslaCam")
    base = os.path.realpath(base) if base else None
    if not base or not base.startswith(root + "/") or not os.path.isdir(base):
        return False, "TeslaCam 目录不存在", 0, [], {}
    stat = {"n": 0, "freed": 0}

    def walk(d, top=False):
        try:
            entries = os.listdir(d)
        except OSError:
            return
        for e in entries:
            p = os.path.join(d, e)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    walk(p)
                    if not (top and e in FM_KEEP_ROOTS):
                        try:
                            os.rmdir(p)
                        except OSError:
                            pass
                else:
                    sz = os.path.getsize(p)
                    os.remove(p)
                    stat["n"] += 1
                    stat["freed"] += sz
            except OSError:
                pass

    walk(base, top=True)
    subprocess.run(["sync"], capture_output=True)
    freed = stat["freed"]
    if freed >= 1073741824:
        sz = "%.2f GB" % (freed / 1073741824.0)
    elif freed >= 1048576:
        sz = "%.1f MB" % (freed / 1048576.0)
    else:
        sz = "%.1f KB" % (freed / 1024.0)
    msg = "已清空 TeslaCam: 删除 %d 个文件, 释放 %s (其他根目录未改动)" % (stat["n"], sz)
    return True, msg, stat["n"], [], {"freed": freed}


def fm_action(area, op, items, dest, name):
    """文件管理器批量操作入口, 返回 (ok, msg, done, failed, extra)"""
    root = _fm_root(area)
    if not root:
        return False, ("车机U盘未连接, 请先点「连接车机U盘」" if area == "usb" else "区域参数错误"), 0, [], {}
    if op == "purge_teslacam":
        if area != "usb":
            return False, "清空 TeslaCam 仅在车机U盘区域可用", 0, [], {}
        return _fm_purge_teslacam(root)
    if not isinstance(items, list) or not items:
        return False, "没有选中任何文件", 0, [], {}
    if len(items) > 200:
        return False, "单次最多操作 200 项", 0, [], {}

    srcs = []
    for it in items:
        rp = _fm_resolve(area, str(it), root)
        if not rp:
            return False, "路径无效或不存在: %s" % it, 0, [], {}
        srcs.append(rp)
    # 父目录先于子项处理时子项会消失, 按深度降序删更稳
    srcs.sort(key=lambda x: x.count(os.sep), reverse=True)
    done, failed = 0, []

    def fail(s, why):
        failed.append(os.path.basename(s) + ": " + why)

    def finish(msg):
        if area == "usb":
            subprocess.run(["sync"], capture_output=True)
        tail = (", %d 项失败" % len(failed)) if failed else ""
        return done > 0, msg + tail, done, failed, {}

    if op == "delete":
        for s in srcs:
            try:
                if os.path.isdir(s) and not os.path.islink(s):
                    shutil.rmtree(s)
                else:
                    os.remove(s)
                done += 1
            except FileNotFoundError:
                done += 1
            except OSError as ex:
                fail(s, ex.strerror or str(ex))
        return finish("已删除 %d 项" % done)

    if op == "rename":
        if len(srcs) != 1:
            return False, "重命名只能选中一项", 0, [], {}
        nn = _fm_name(name)
        if not nn:
            return False, "新名称无效", 0, [], {}
        s = srcs[0]
        d = os.path.join(os.path.dirname(s), nn)
        if os.path.lexists(d):
            return False, "目标名称已存在: " + nn, 0, [], {}
        try:
            os.rename(s, d)
            done = 1
            return finish("已重命名为 " + nn)
        except OSError as ex:
            return False, "重命名失败: " + (ex.strerror or str(ex)), 0, [], {}

    if op in ("move", "copy"):
        dd = _fm_dest_dir(area, dest, root)
        if not dd:
            return False, "目标目录不存在或不在允许范围内", 0, [], {}
        verb = "移动" if op == "move" else "复制"
        for s in srcs:
            d = os.path.join(dd, os.path.basename(s))
            if os.path.lexists(d):
                fail(s, "目标已存在")
                continue
            if os.path.isdir(s) and (d == s or d.startswith(s + "/")):
                fail(s, "不能%s到自身内部" % verb)
                continue
            try:
                if op == "move":
                    shutil.move(s, d)
                elif os.path.isdir(s):
                    shutil.copytree(s, d, symlinks=True)
                else:
                    shutil.copy2(s, d)
                done += 1
            except (OSError, shutil.Error) as ex:
                fail(s, str(ex)[-80:])
        return finish("已%s %d 项" % (verb, done))

    return False, "未知操作: %s" % op, 0, [], {}


def _range_bounds(hdr, size):
    """解析 Range 头, 返回 (start, end) / None(整体) / 'BAD'(格式错) / 'UNSAT'(越界)
    iOS/Safari 的下载器会先发 bytes=-N (取尾部) 探一下 moov, 必须支持,
    否则连接会被直接掐断, 手机端只会存到前一小段 (表现为"下载下来只有 1 秒")。"""
    if not hdr:
        return None
    m = re.match(r"bytes=(\d*)-(\d*)$", (hdr or "").strip())
    if not m:
        return "BAD"
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return "BAD"
    if a == "":                       # bytes=-N: 最后 N 字节
        n = int(b)
        if n <= 0 or size <= 0:
            return "UNSAT"
        return (max(0, size - n), size - 1)
    start = int(a)
    if start >= size:
        return "UNSAT"
    end = int(b) if b else size - 1
    if end < start:
        return "UNSAT"
    return (start, min(end, size - 1))


def serve_file(self, fp, head_only=False):
    """流式发送文件 (支持 Range / HEAD), 用于下载"""
    fn = os.path.basename(fp)
    ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
    ct = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
          "css": "text/css", "html": "text/html", "htm": "text/html", "txt": "text/plain",
          "json": "application/json", "pdf": "application/pdf", "svg": "image/svg+xml",
          "mp4": "video/mp4", "mp3": "audio/mpeg", "wav": "audio/wav"}.get(ext, "application/octet-stream")
    try:
        size = os.path.getsize(fp)
    except OSError:
        self._send(404, b"not found")
        return
    rb = _range_bounds(self.headers.get("Range"), size)
    if rb in ("BAD", "UNSAT"):
        self.send_response(416 if rb == "UNSAT" else 400)
        if rb == "UNSAT":
            self.send_header("Content-Range", "bytes */%d" % size)
        self.send_header("Content-Length", "0")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return
    if rb:
        start, end = rb
    else:
        start, end = 0, size - 1
    length = max(0, end - start + 1)
    self.send_response(206 if rb else 200)
    self.send_header("Content-Type", ct)
    self.send_header("Content-Length", str(length))
    self.send_header("Accept-Ranges", "bytes")
    self.send_header("Content-Disposition", 'attachment; filename="%s"' % urllib.parse.quote(fn))
    if rb:
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
    self.end_headers()
    if head_only:
        return
    with open(fp, "rb") as f:
        f.seek(start)
        remain = length
        while remain > 0:
            chunk = f.read(min(65536, remain))
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            remain -= len(chunk)


LIVE_SCRIPT = "tesla-live-sync.py"
LIVE_LOG = "/root/tesla-live.log"
LIVE_STATE = "/root/.tesla-live-state.json"


def _proc_running(needle):
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    if needle.encode() in f.read():
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False


_ARC_SIZE_CACHE = {"t": 0.0, "v": 0}


def archive_size():
    """归档区实际占用。os.walk 在文件多的时候要几十~几百毫秒, 而存储卡每 10 秒轮询一次,
    所以做 60 秒缓存, 别让面板把设备拖慢。"""
    now = time.time()
    if _ARC_SIZE_CACHE["t"] and now - _ARC_SIZE_CACHE["t"] < 60:
        return _ARC_SIZE_CACHE["v"]
    sz = _dir_size(ARCHIVE) if os.path.isdir(ARCHIVE) else 0
    _ARC_SIZE_CACHE.update(t=now, v=sz)
    return sz


def disk_usage():
    """存储卡三行数据: 虚拟U盘(镜像实占) / 归档区 / 整盘总占用

    三行统一用「整盘容量」当分母, 所以三条进度条可以直接对比, 前两条加起来
    约等于第三条 (差的几百 MB 是系统/日志等其它占用)。"""
    d = {}
    # 虚拟U盘: 稀疏镜像文件在宿主盘上的真实占用; 标称容量另给 (两者通常不等)
    try:
        im = os.stat(IMG)
        d["img_nominal"] = im.st_size
        d["img_real"] = getattr(im, "st_blocks", 0) * 512
    except OSError:
        d["img_nominal"] = d["img_real"] = 0
    d["arc_used"] = archive_size()
    # 整盘 = 镜像文件所在分区 (镜像和归档同一块盘)
    d["disk_total"] = d["disk_used"] = d["disk_free"] = 0
    for p in (os.path.dirname(IMG) or "/", ARCHIVE):
        try:
            hf = os.statvfs(p)
        except OSError:
            continue
        if hf.f_blocks <= 0:
            continue
        fr = hf.f_frsize
        d["disk_total"] = hf.f_blocks * fr
        d["disk_free"] = hf.f_bavail * fr
        d["disk_used"] = (hf.f_blocks - hf.f_bavail) * fr
        break
    # 归档区剩余空间 = 整盘剩余 (同分区)
    d["arc_free"] = d["disk_free"]
    # 兼容旧字段: 挂载时 = 车载U盘 (exFAT) 内部用量, 否则 = 整盘
    d["src"], d["used"], d["total"] = "loop", d["disk_used"], d["disk_total"]
    for p, usb in ((MNT, True), (ARCHIVE, False)):
        if usb and not os.path.ismount(MNT):
            # MNT 目录本身常驻 (tmpfs), 未挂载镜像时 statvfs 会读到 tmpfs 的
            # 小容量 (如 0.7G), 必须跳过
            continue
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        if st.f_blocks <= 0:
            continue
        fr = st.f_frsize
        d["src"] = "usb" if usb else "loop"
        d["used"] = (st.f_blocks - st.f_bavail) * fr
        d["total"] = st.f_blocks * fr
        break
    if d["disk_total"] <= 0 and d["arc_used"] <= 0:
        return None
    return d


def live_status():
    """车机在线只读同步的健康状态 (给页面显示心跳)"""
    running = _proc_running(LIVE_SCRIPT)
    pending = 0
    try:
        with open(LIVE_STATE) as f:
            pending = len(json.load(f).get("__pending__", {}))
    except Exception:
        pass
    ago, last = None, ""
    try:
        st = os.stat(LIVE_LOG)
        ago = int(time.time() - st.st_mtime)
        with open(LIVE_LOG, "rb") as f:
            f.seek(max(0, st.st_size - 2048))
            lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
        for line in reversed(lines):
            if "已同步" in line:
                last = line.split("] ", 1)[-1]
                break
        if not last and lines:
            last = lines[-1].split("] ", 1)[-1]
    except Exception:
        pass
    return {"running": running, "pending": pending, "ago": ago, "last": last, "disk": disk_usage()}


def active_skin():
    ap = os.path.join(SKIN_DIR, "active.json")
    try:
        with open(ap) as f:
            return json.load(f).get("skin")
    except Exception:
        return None


# ---------------- 多路合成导出 (ffmpeg) ----------------
FFMPEG_BIN = os.environ.get("TSF_FFMPEG") or (
    "/opt/mu300/ffmpeg/ffmpeg" if os.path.exists("/opt/mu300/ffmpeg/ffmpeg") else "ffmpeg")
FFPROBE_BIN = os.environ.get("TSF_FFPROBE") or (
    "/opt/mu300/ffmpeg/ffprobe" if os.path.exists("/opt/mu300/ffmpeg/ffprobe") else "ffprobe")
STITCH_DIR = os.environ.get("TSF_CACHE", "/opt/mu300") + "/stitch-tmp"  # 不用 /tmp: tmpfs 占内存, 大视频会挤爆 RAM
STITCH_JOBS = {}
STITCH_LOCK = threading.Lock()
# 视频流并发上限: 事件多的时候浏览器会一口气请求上百个预览, 不限流会拖死设备
MEDIA_SEM = threading.BoundedSemaphore(int(os.environ.get("TSF_MEDIA_CONN", "8")))
MEDIA_ACTIVE = [0]        # 正在推送的视频流数量: 自动升级要等它归零, 别打断正在看的人
THUMB_DIR = os.environ.get("TSF_CACHE", "/opt/mu300") + "/thumbs"
THUMB_SEM = threading.BoundedSemaphore(2)
THUMB_W = 400


def thumb_file(fp, ss_t=1.0):
    """抽指定秒数那一帧并缓存在 eMMC, 返回 jpg 路径; 抽不出来返回 None。
    列表缩略图 (/thumb) 和飞书推送封面共用。"""
    try:
        st = os.stat(fp)
    except OSError:
        return None
    key = hashlib.md5(("%s|%d|%d|%g" % (fp, st.st_size, int(st.st_mtime), ss_t)).encode()).hexdigest()
    out = os.path.join(THUMB_DIR, key + ".jpg")
    try:                               # 0 字节是上次 ffmpeg 失败留下的残骸, 视为无效缓存
        if os.path.getsize(out) == 0:
            os.remove(out)
    except OSError:
        pass
    if os.path.isfile(out):
        return out
    try:
        os.makedirs(THUMB_DIR, exist_ok=True)
    except OSError:
        pass
    tmp = os.path.join(THUMB_DIR, "%s.%d.tmp.jpg" % (key, threading.get_ident()))
    if not THUMB_SEM.acquire(timeout=12):
        return None
    try:
        if os.path.isfile(out):
            return out
        # 指定秒数可能落在片段末尾之外(触发帧偏移略超时长), 逐级回落
        for ss in ("%.1f" % ss_t, "1", "0"):
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
                subprocess.run([FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
                                "-ss", ss, "-i", fp, "-frames:v", "1",
                                "-vf", "scale=%d:-2" % THUMB_W, "-q:v", "7", tmp],
                               capture_output=True, timeout=30)
                if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                    os.replace(tmp, out)
                    return out
            except Exception:
                pass
        return None
    finally:
        THUMB_SEM.release()
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
# ---------- 在线清晰度: 按需转码 + eMMC 缓存 (设备端单任务串行, 防止重转码打崩内核) ----------
# 源 1280x960 (4:3); 用固定目标码率: 公网 5G 上行只有 ~120KB/s, CRF 模式码率不可控会传不完
# 8 核 T760 实测: -threads 8 + superfast 比 2 线程快一倍 (20s->10s/分钟片段), 内存峰值充足 (720p 后仍余 ~980MB)
RES_SCALE = {"360p": (480, 360, 500), "480p": (640, 480, 800), "720p": (960, 720, 1500)}  # 宽,高,kbps
RES_DIR = os.environ.get("TSF_CACHE", "/opt/mu300") + "/res-cache"
try:
    os.makedirs(RES_DIR, exist_ok=True)
except OSError:
    pass
Q_SEM = threading.BoundedSemaphore(int(os.environ.get("TSF_TR_CODES", "2")))  # 并发转码上限: 8核允许2个×4线程, 多路点开排队时间减半
RES_MAX = 2 * 1024 * 1024 * 1024  # 清晰度缓存总量上限 2GB, 超了从最旧开始删


def res_cleanup():
    try:
        now = time.time()
        for f in os.listdir(RES_DIR):  # 清理残留转码临时文件 (进程被杀会留下 0 字节 tmp)
            if f.endswith(".tmp") and now - os.path.getmtime(os.path.join(RES_DIR, f)) > 1800:
                try:
                    os.unlink(os.path.join(RES_DIR, f))
                except OSError:
                    pass
        files = [os.path.join(RES_DIR, f) for f in os.listdir(RES_DIR)
                 if f.endswith(".mp4")]
        total = sum(os.path.getsize(f) for f in files if os.path.isfile(f))
        if total <= RES_MAX:
            return
        files.sort(key=lambda f: os.path.getmtime(f))
        for f in files:
            if total <= RES_MAX:
                break
            sz = os.path.getsize(f)
            os.unlink(f)
            total -= sz
    except Exception:
        pass


# ---------- 归档滚动窗口 + 磁盘空间兜底 ----------
# 车机 U 盘里的 RecentClips 本身就是"滚动缓冲": 只留最近约 1 小时, 更旧的由车机自己覆盖。
# 但盒子这边过去是每来一段就抄一份、永久留着, 而哨兵开着时车整夜不睡, 4 路约 140MB/分钟
# (8.4GB/小时), 一晚上就能吃掉几十 GB —— 这才是盒子镜像被撑爆的真正原因。
# 现在让盒子副本按同样窗口滚动: 只留最近 ROLL_MIN 分钟, 更旧的从盒子归档里删掉。
# 车机 U 盘那份完全不碰 (同步脚本只读 U 盘, 靠 exFAT 解析器读, 从不删源文件);
# SentryClips / SavedClips 属于哨兵事件 + 用户主动保存的录像, 由下方 BOX_KEEP_DAYS /
# BOX_MAX_GB 定期清理管理 (只删盒子归档副本, 车机 U 盘那份同样不碰)。
RECENT_ROLL_MIN = int(os.environ.get("TSF_RECENT_ROLL_MIN", "60"))    # 归档滚动窗口(分钟), 0=关闭滚动
FREE_MIN_MB = int(os.environ.get("TSF_FREE_MIN_MB", "4096"))          # 剩余低于此值开始兜底清理
FREE_TARGET_MB = int(os.environ.get("TSF_FREE_TARGET_MB", "10240"))   # 兜底清理到恢复到该值为止
RECENT_KEEP_MIN = int(os.environ.get("TSF_RECENT_KEEP_MIN", "60"))    # 兜底清理时最近 N 分钟必留
# 盒子侧 SentryClips / SavedClips 定期清理 (方案B):
# 哨兵开着时 4 路约 8.4GB/小时, 哨兵事件 + 手动保存的录像只增不删, 盘迟早被吃满。
# 车机 U 盘那份永远不碰, 这里只清盒子归档副本。两个维度独立生效:
#   1) 按天数: 只保留最新事件往前 N 天内的归档 (锚点是最新事件而不是当前时间,
#      停车几天没新事件不会被误清空);
#   2) 按体积: 单个分类超过 MAX_GB 就从最旧的开始删, 直到回到上限以内。
#      teslacam.img 高水位约 170G, loop 盘 207.7G, 盒子归档预算约 12G/类比较安全。
# 同步进行中的目录 (1 小时内有改动) 跳过, 非时间戳命名的目录一律不动。
# 清掉的分类/事件会记入 /archive/.pruned 墓碑文件, 同步端读到就跳过 —— 否则车机 U 盘上
# 还留着的旧事件会被当成新片段重抄回来, 形成 删除→重抄 的死循环 (和 RecentClips 同理)。
BOX_KEEP_DAYS = int(os.environ.get("TSF_BOX_KEEP_DAYS", "14"))        # 盒子侧哨兵/手动保存保留天数, 0=不按天数清
BOX_MAX_GB = int(os.environ.get("TSF_BOX_MAX_GB", "12"))              # 盒子侧单个分类体积上限(GB), 0=不按体积清
PRUNED_FILE = os.path.join(ARCHIVE, ".pruned")                        # 墓碑: 每行 "分类/事件名", 同步端跳过


def free_mb(p):
    try:
        st = os.statvfs(p)
        return st.f_bavail * st.f_frsize // 1048576
    except OSError:
        return -1


def _ev_dt(name):
    """事件目录名就是片段起始时间 (2026-11-05_08-00-23)。
    用文件名里的车机时间来比较, 不受盒子时区设错的影响"""
    try:
        return datetime.datetime.strptime(name[:19], "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None


def roll_recent():
    """盒子侧 RecentClips 按滚动窗口自动淘汰 (只删盒子归档, 不碰车机 U 盘)"""
    rd = os.path.join(ARCHIVE, "RecentClips")
    if RECENT_ROLL_MIN <= 0 or not os.path.isdir(rd):
        return 0
    evs = []
    for e in os.listdir(rd):
        dt = _ev_dt(e)
        if dt:                       # 非时间戳命名的目录一律不动
            evs.append((dt, e))
    if len(evs) < 2:
        return 0
    evs.sort()
    # 以"最新片段"为锚点而不是当前时间: 车休眠/哨兵关闭期间没有新片段, 就不会被误清空
    cutoff = evs[-1][0] - datetime.timedelta(minutes=RECENT_ROLL_MIN)
    n = 0
    for dt, e in evs:
        if dt < cutoff:
            shutil.rmtree(os.path.join(rd, e), ignore_errors=True)
            n += 1
    return n


def _dir_size(p):
    sz = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                sz += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return sz


def _tombstone(cat, ev):
    """记录已清理的事件, 同步端读到就跳过, 防止 删除→重抄 死循环"""
    try:
        with open(PRUNED_FILE, "a", encoding="utf-8") as f:
            f.write("%s/%s\n" % (cat, ev))
    except OSError:
        pass


def roll_box():
    """盒子侧 SentryClips / SavedClips 定期清理 (按天数 + 按体积), 只删盒子归档, 不碰车机 U 盘"""
    n = 0
    for cat in ("SentryClips", "SavedClips"):
        base = os.path.join(ARCHIVE, cat)
        if not os.path.isdir(base):
            continue
        evs = []
        for e in os.listdir(base):
            dt = _ev_dt(e)
            if dt:                       # 非时间戳命名的目录一律不动
                evs.append((dt, e))
        if not evs:
            continue
        evs.sort()
        # 目录统计 (1 小时内有改动的跳过: 可能还在同步写入)
        sized = []
        for dt, e in evs:
            p = os.path.join(base, e)
            try:
                if time.time() - os.path.getmtime(p) < 3600:
                    continue
                sized.append((dt, e, p, _dir_size(p)))
            except OSError:
                continue
        # 1) 按天数: 锚点 = 该分类最新事件, 只留最近 BOX_KEEP_DAYS 天
        if BOX_KEEP_DAYS > 0:
            cutoff = evs[-1][0] - datetime.timedelta(days=BOX_KEEP_DAYS)
            for dt, e, p, _sz in sized:
                if dt < cutoff and os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                    if not os.path.isdir(p):
                        _tombstone(cat, e)
                        n += 1
        # 2) 按体积: 超出 BOX_MAX_GB 从最旧开始删
        if BOX_MAX_GB > 0:
            left = [(dt, e, p, sz) for dt, e, p, sz in sized if os.path.isdir(p)]
            left.sort()
            total = sum(sz for _, _, _, sz in left)
            budget = BOX_MAX_GB * (1 << 30)
            for dt, e, p, sz in left:
                if total <= budget:
                    break
                shutil.rmtree(p, ignore_errors=True)
                total -= sz
                if not os.path.isdir(p):
                    _tombstone(cat, e)
                    n += 1
    return n


def health_logger():
    """每分钟记一条 内存/负载/ffmpeg 进程数 到持久日志 (环形, 最多 2000 行)。
    内核 lockup 重启会把 dmesg 清空, 这里是唯一能复盘"死前发生了什么"的证据。"""
    fp = "/opt/tesla-sentry/health.log"
    while True:
        try:
            mem = load = "?"
            with open("/proc/meminfo", encoding="ascii") as f:
                for ln in f:
                    if ln.startswith("MemAvailable"):
                        mem = "%dMB" % (int(ln.split()[1]) // 1024)
                        break
            with open("/proc/loadavg", encoding="ascii") as f:
                load = " ".join(f.read().split()[:3])
            ff = 0
            try:
                for pid in os.listdir("/proc"):
                    if not pid.isdigit():
                        continue
                    try:
                        with open("/proc/%s/comm" % pid, encoding="ascii") as f:
                            if f.read().strip() == "ffmpeg":
                                ff += 1
                    except OSError:
                        continue
            except OSError:
                pass
            with open(fp, "a", encoding="utf-8") as f:
                f.write("%s mem=%s load=%s ffmpeg=%d\n" % (
                    time.strftime("%m-%d %H:%M"), mem, load, ff))
            if os.path.getsize(fp) > 300 * 1024:      # 环形截断, 只留最近 ~2000 行
                try:
                    with open(fp, encoding="utf-8") as f:
                        tail = f.readlines()[-2000:]
                    with open(fp, "w", encoding="utf-8") as f:
                        f.writelines(tail)
                except OSError:
                    pass
        except Exception:
            pass
        time.sleep(60)


def disk_guard():
    rd = os.path.join(ARCHIVE, "RecentClips")
    while True:
        try:
            roll_recent()
        except Exception:
            pass
        try:   # 盒子侧哨兵/手动保存归档定期清理 (方案B)
            roll_box()
        except Exception:
            pass
        try:   # 兜底: 万一别的数据把盘吃满, 再按空间淘汰最旧的
            if free_mb(ARCHIVE) < FREE_MIN_MB and os.path.isdir(rd):
                cutoff = time.time() - RECENT_KEEP_MIN * 60
                for e in sorted(os.listdir(rd)):   # 目录名即时间戳, 天然按时间排序
                    if free_mb(ARCHIVE) >= FREE_TARGET_MB:
                        break
                    p = os.path.join(rd, e)
                    try:
                        if not os.path.isdir(p) or os.path.getmtime(p) > cutoff:
                            continue
                    except OSError:
                        continue
                    shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass
        time.sleep(300)


# ---------- 飞书推送: 哨兵触发时把信息和触发画面推到群里 ----------
# 机器人 webhook 只能发文字卡片; 要带封面图必须配「自建应用」的 app_id/app_secret
# (应用需开机器人能力 + im:message/im:resource 权限), 由本程序换取 token 上传图片拿 img_key。
NOTIFY_CONF = os.environ.get("TSF_NOTIFY_CONF", "/opt/tesla-sentry/notify.json")
NOTIFY_STATE = os.environ.get("TSF_NOTIFY_STATE", "/opt/tesla-sentry/notify-state.json")
FEISHU_API = "https://open.feishu.cn/open-apis"
_NC_DEFAULT = {
    "enabled": False, "mode": "dm", "webhook": "", "app_id": "", "app_secret": "",
    "receive_id": "", "receive_id_type": "", "public_url": "", "saved": False, "delay": 20,
    "media": "thumb", "quality": "480p", "vdur": 30,
}
_TOK = {"v": "", "exp": 0}
CAM_NUM = {0: "前视", 3: "左B柱", 4: "右B柱", 5: "左翼子板", 6: "右翼子板", 7: "后视"}
CAM_SUFFIX = {0: "front", 3: "left_pillar", 4: "right_pillar",
              5: "left_repeater", 6: "right_repeater", 7: "back"}
REASON_DETAIL = {
    "sentry_aware_object_detection": "侦测到物体移动",
    "sentry_aware_accel": "侦测到碰撞/震动",
    "sentry_aware_door": "车门被拉动",
    "sentry_aware_handle": "门把手被拉动",
    "sentry_panic": "遭遇袭击 (紧急报警)",
    "sentry_trigger": "哨兵模式触发",
    "user_interaction_honk": "按喇叭保存",
    "user_interaction_dashcam_saved": "手动保存",
    "user_interaction_dashcam_menu": "行车记录仪菜单保存",
}


def notify_conf():
    c = dict(_NC_DEFAULT)
    try:
        with open(NOTIFY_CONF, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict):
            c.update(j)
    except Exception:
        pass
    return c


def notify_save(patch):
    c = notify_conf()
    for k, v in (patch or {}).items():
        if k in _NC_DEFAULT:
            c[k] = v
    try:
        os.makedirs(os.path.dirname(NOTIFY_CONF) or ".", exist_ok=True)
        tmp = NOTIFY_CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
        try:
            os.chmod(tmp, 0o600)          # 里面存着 app_secret, 只给 root 读
        except OSError:
            pass
        os.replace(tmp, NOTIFY_CONF)
    except OSError:
        pass
    return c


def _nstate_load():
    try:
        with open(NOTIFY_STATE, encoding="utf-8") as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _nstate_save(st):
    try:
        os.makedirs(os.path.dirname(NOTIFY_STATE) or ".", exist_ok=True)
        tmp = NOTIFY_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, NOTIFY_STATE)
    except OSError:
        pass


def _reason_info(reason):
    """reason → (分类, 卡片主题色, 详情)"""
    r = str(reason or "")
    detail = REASON_DETAIL.get(r) or ("未标注原因" if not r else r)
    if r.startswith(("sentry_panic", "sentry_aware_accel")):
        return "遭遇袭击", "red", detail
    if r.startswith(("sentry_aware_door", "sentry_aware_handle")):
        return "车门拉动", "orange", detail
    if r.startswith("sentry_"):
        return "哨兵警觉", "blue", detail
    if r.startswith("user_interaction"):
        return "手动保存", "grey", detail
    return "其他事件", "grey", detail


def _ev_files(d):
    try:
        return sorted(f for f in os.listdir(d) if f.lower().endswith(".mp4"))
    except OSError:
        return []


def _pick_clip(cat, name):
    """按 event.json 的 camera + timestamp 找出触发摄像头那一路的 mp4, 返回 (文件路径, 触发偏移秒, meta);
    对不上时回落到前视。找不到任何片段返回 (None, 0, meta)。"""
    d = os.path.join(ARCHIVE, cat, name)
    meta = {}
    try:
        with open(os.path.join(d, "event.json"), encoding="utf-8", errors="ignore") as f:
            meta = json.load(f) or {}
    except Exception:
        meta = {}
    files = _ev_files(d)
    if not files:
        return None, 0.0, meta
    cam_key = CAM_SUFFIX.get(int(meta.get("camera")) if str(meta.get("camera", "")).strip().isdigit() else -1)
    ts = None
    try:
        ts = datetime.datetime.strptime(str(meta.get("timestamp"))[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        ts = None
    if ts is not None:
        for f in files:                       # 文件名前 19 位是片段开始时间, 与 timestamp 同一时基
            try:
                s = datetime.datetime.strptime(f[:19], "%Y-%m-%d_%H-%M-%S")
            except ValueError:
                continue
            off = (ts - s).total_seconds()
            if 0 <= off <= 70 and f.rsplit("-", 1)[-1][:-4] == cam_key:
                return os.path.join(d, f), max(0.0, min(off, 60.0)), meta
    front = [f for f in files if f.endswith("-front.mp4")]
    return os.path.join(d, front[0] if front else files[0]), 1.0, meta


def _trigger_frame(cat, name):
    """触发摄像头那一帧的封面图; 返回 (jpg 路径, meta)"""
    fp, off, meta = _pick_clip(cat, name)
    if not fp:
        return None, meta
    return thumb_file(fp, off), meta


def _post_json(url, payload, timeout=12, headers=None):
    """POST JSON 并解析返回。
    注意: 这里用 http.client 而不是 urllib — urllib 会给请求带上 Connection: close,
    飞书网关对这种短连接 POST 会直接断链 (SSL BAD_RECORD_MAC), 实测 http.client 正常。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    return json.loads(_http_post(url, body, h, timeout).decode("utf-8", "replace"))


def _tls12():
    """给飞书 API 用的 SSLContext: 强制 TLS 1.2。
    实测在 F50 的 4G 链路上 TLS 1.3 会随机丢/坏记录 (SSLV3_ALERT_BAD_RECORD_MAC, 约 1/3 概率),
    同一请求降到 TLS 1.2 后 100% 成功, 所以对飞书固定用 1.2。"""
    global _TLS_CTX
    if _TLS_CTX is None:
        c = ssl.create_default_context()
        try:
            c.maximum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            pass
        _TLS_CTX = c
    return _TLS_CTX


_TLS_CTX = None


def _http_post(url, body, headers=None, timeout=12):
    """发一个 POST, 返回响应体 bytes; HTTP 4xx/5xx 也照常返回内容 (里面有飞书的错误码)
    TLS 抖动会自动重试 3 次 (每次新建连接)。"""
    u = urllib.parse.urlsplit(url)
    last = None
    for attempt in range(3):
        conn = None
        try:
            if u.scheme == "https":
                conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout,
                                                   context=_tls12())
            elif u.scheme == "http":
                conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
            else:
                raise RuntimeError("不支持的地址: %s" % url[:40])
            path = (u.path or "/") + (("?" + u.query) if u.query else "")
            conn.request("POST", path, body=body, headers=dict(headers or {}))
            r = conn.getresponse()
            return r.read() or b"{}"
        except (ssl.SSLError, ConnectionError, OSError) as ex:
            last = ex
            if attempt < 2:
                time.sleep(0.4 * (attempt + 1))
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
    raise last if last else RuntimeError("POST 失败")


def _feishu_token(conf):
    if _TOK["v"] and _TOK["exp"] > time.time() + 60:
        return _TOK["v"]
    j = _post_json(FEISHU_API + "/auth/v3/tenant_access_token/internal",
                   {"app_id": conf.get("app_id", ""), "app_secret": conf.get("app_secret", "")})
    if j.get("code") != 0 or not j.get("tenant_access_token"):
        raise RuntimeError("取 token 失败: %s" % (j.get("msg") or j))
    _TOK["v"] = j["tenant_access_token"]
    _TOK["exp"] = time.time() + max(600, int(j.get("expire") or 7200) - 300)
    return _TOK["v"]


def _feishu_upload(conf, jpg):
    """上传封面图, 返回 image_key (失败抛异常)"""
    with open(jpg, "rb") as f:
        data = f.read()
    bd = "----tsf%s" % hashlib.md5(("%s%.6f" % (jpg, time.time())).encode()).hexdigest()
    pre = ("--%s\r\nContent-Disposition: form-data; name=\"image_type\"\r\n\r\nmessage\r\n"
           "--%s\r\nContent-Disposition: form-data; name=\"image\"; filename=\"cover.jpg\"\r\n"
           "Content-Type: image/jpeg\r\n\r\n" % (bd, bd)).encode()
    body = pre + data + ("\r\n--%s--\r\n" % bd).encode()
    raw = _http_post(FEISHU_API + "/im/v1/images", body, {
        "Authorization": "Bearer " + _feishu_token(conf),
        "Content-Type": "multipart/form-data; boundary=" + bd,
    }, timeout=30)
    j = json.loads(raw.decode("utf-8", "replace"))
    key = ((j or {}).get("data") or {}).get("image_key")
    if j.get("code") != 0 or not key:
        raise RuntimeError("上传封面失败: %s" % (j.get("msg") or j))
    return key


VID_Q = {"720p": (720, 1500), "480p": (480, 800), "360p": (360, 500)}   # 输出高度, 目标码率 kbps

FONT_DIR = os.environ.get("TSF_FONTS") or "/opt/tesla-sentry/fonts"


def _wm_font():
    """水印用的中文字体 (libass 经 fontsdir 加载); 没有则返回空"""
    try:
        for n in sorted(os.listdir(FONT_DIR)):
            if n.lower().endswith((".ttf", ".otf", ".ttc")):
                return os.path.join(FONT_DIR, n)
    except OSError:
        pass
    return ""


def _wm_fontconf():
    """给 ffmpeg 自带的 fontconfig 一个最小配置 (OpenWrt 上没有 /etc/fonts/fonts.conf,
    会导致 libass 的字体选择整个失效, 连矢量图形都不画)。返回配置文件路径。"""
    p = os.path.join(FONT_DIR, "fonts.conf")
    try:
        os.makedirs(FONT_DIR, exist_ok=True)
        if not os.path.isfile(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0"?>\n'
                        '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
                        '<fontconfig>\n  <dir>%s</dir>\n  <cachedir>/tmp/fccache</cachedir>\n'
                        '</fontconfig>\n' % FONT_DIR)
    except OSError:
        return ""
    return p


def _wm_tw(text, fs):
    """粗略估算文字宽度 (只用于胶囊宽度与槽位排布, 差几 px 不影响观感)"""
    w = 0.0
    for ch in str(text):
        if ord(ch) > 0x2E80:
            w += 1.0
        elif ch == " ":
            w += 0.28
        elif ch in "·:":
            w += 0.33
        elif ch == "%":
            w += 0.92
        elif ch.isdigit():
            w += 0.57
        elif ch.isupper():
            w += 0.68
        else:
            w += 0.52
    return w * fs


def _ass_pts(pts):
    def n(v):
        return ("%.1f" % v).rstrip("0").rstrip(".")
    return "m " + " l ".join("%s %s" % (n(x), n(y)) for x, y in pts)


def _ass_circle(cx, cy, r, steps=32):
    return _ass_pts([(cx + r * math.cos(2 * math.pi * i / steps),
                      cy + r * math.sin(2 * math.pi * i / steps)) for i in range(steps)])


def _ass_rrect(x, y, w, h, r, steps=6):
    """圆角矩形 (胶囊) 矢量路径"""
    r = min(r, w / 2.0, h / 2.0)
    pts = []
    for cx0, cy0, a0, a1 in ((x + w - r, y + r, -90, 0), (x + w - r, y + h - r, 0, 90),
                             (x + r, y + h - r, 90, 180), (x + r, y + r, 180, 270)):
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * float(i) / steps)
            pts.append((cx0 + r * math.cos(a), cy0 + r * math.sin(a)))
    return _ass_pts(pts)


def _ass_wheel(cx, cy, r, seg=16):
    """AP 方向盘徽标的两条矢量路径 (ASS p1 绘图): (圆形底, 方向盘图形)。
    圆环用多段四边形拼出, 不依赖 libass 的填充规则。"""
    badge = _ass_circle(cx, cy, r, 24)
    ir = r * 0.60
    lw = max(1.5, r * 0.17)
    parts = []
    for i in range(seg):
        a1, a2 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        o1 = (cx + (ir + lw / 2) * math.cos(a1), cy + (ir + lw / 2) * math.sin(a1))
        o2 = (cx + (ir + lw / 2) * math.cos(a2), cy + (ir + lw / 2) * math.sin(a2))
        i1 = (cx + (ir - lw / 2) * math.cos(a1), cy + (ir - lw / 2) * math.sin(a1))
        i2 = (cx + (ir - lw / 2) * math.cos(a2), cy + (ir - lw / 2) * math.sin(a2))
        parts.append(_ass_pts([i1, o1, o2, i2]))
    b = lw / 2.0
    parts.append(_ass_pts([(cx - ir, cy - b), (cx + ir, cy - b), (cx + ir, cy + b), (cx - ir, cy + b)]))
    parts.append(_ass_pts([(cx - b, cy), (cx - b, cy + ir), (cx + b, cy + ir), (cx + b, cy)]))
    parts.append(_ass_circle(cx, cy, max(1.4, r * 0.14), 12))
    return badge, " ".join(parts)


def _wm_ass(fp, ss, dur, meta, size=(1280, 720)):
    """生成与网页播放器同款的行车信息水印 ASS 字幕 (导出视频用):
    顶部淡色窄条 + 左侧「日期时间 城市·街道」+ 右侧「AP 方向盘 / 车速 km/h / 转向灯 / 挡位 / 刹车 / 油门」。
    与前端 drawWm() 一一对应, 只是全部改成矢量色块 + 文字。无遥测/无字体返回 None。"""
    if not _wm_font():
        return None
    tel = telemetry_cached(fp)
    if not tel or not tel.get("frames"):
        return None
    frames = tel["frames"]
    try:
        t0 = datetime.datetime.strptime(os.path.basename(fp)[:19], "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        t0 = None
    loc = " · ".join(x for x in (meta.get("city") or "", meta.get("street") or "") if x).strip()
    dur = int(dur)
    if dur < 1:
        return None
    W, H = size if size else (1280, 720)
    W, H = max(320, int(W)), max(240, int(H))
    # 水印大小跟画面高度走 (同样高度下观感一致, 4:3 的 960x720 也不会显得过小);
    # 极窄画面再用宽度兜底, 免得右侧元素挤出画外
    s = min(H / 720.0, W / 900.0)

    pad = int(round(14 * s))
    bh = int(round(46 * s))                  # 顶部窄条高度
    yc = bh // 2
    f_date, f_loc = max(9, int(round(14 * s))), max(8, int(round(13 * s)))
    f_pill = max(8, int(round(12 * s)))      # 胶囊字号
    f_num = max(10, int(round(22 * s)))      # 车速
    f_unit = max(7, int(round(11 * s)))      # km/h
    f_gear = max(8, int(round(14 * s)))      # 挡位字母
    ph = int(round(24 * s))                  # 胶囊高
    ppad = int(round(18 * s))                # 胶囊左右内边距
    gap8, gap12 = int(round(8 * s)), int(round(12 * s))

    # ---- 右侧槽位从右往左一次算好, 每秒复用 (数字变宽只往左长, 不挤动别人) ----
    x = W - pad
    thr_w = max(ph * 2, int(round(_wm_tw("100%", f_pill) + ppad)))
    thr_x = x - thr_w
    x = thr_x - gap8
    brk_w = max(ph * 2, int(round(_wm_tw("刹车", f_pill) + ppad)))
    brk_x = x - brk_w
    x = brk_x - gap12
    gr = int(round(13 * s))                  # 挡位圆半径
    gear_cx = x - gr
    x = gear_cx - gr - int(round(10 * s))
    tri = int(round(9 * s))
    rtri_x = x - tri                         # 右转向灯 (尖朝右)
    x = rtri_x - int(round(7 * s))
    ltri_x = x - tri                         # 左转向灯 (尖朝左)
    x = ltri_x - gap12
    unit_w = int(round(_wm_tw("km/h", f_unit) * 1.06)) + 2   # 略放大: km/h 用左锚, 尾部留白更安全
    x -= unit_w + int(round(6 * s))
    num_right = x                            # 车速右对齐线 = km/h 左锚基准
    R = int(round(11 * s))                   # AP 方向盘半径
    wheel_gap = int(round(9 * s))            # 方向盘与车速之间的间距
    x -= int(round(_wm_tw("000", f_num))) + wheel_gap + R * 2

    C_WHITE, C_SOFT = "&H00FFFFFF&", "&H00F8F3EE&"
    C_THR, C_BRK, C_BRK_OFF = "&H00E07A1F&", "&H003A45FF&", "&H00322820&"
    C_GEAR, C_TRI_ON, C_TRI_OFF = "&H0059C734&", "&H000B9EF5&", "&H005F544A&"
    C_AP_ON, C_AP_OFF, C_AP_GLY = "&H000B9EF5&", "&H00382E26&", "&H00D6CCC3&"

    def esc(v):
        return str(v).replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")

    def ts(n):
        n = int(n)
        return "%d:%02d:%02d.000" % (n // 3600, n % 3600 // 60, n % 60)

    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % W, "PlayResY: %d" % H,
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # Wm: 带描边投影的文字; WmP: 胶囊/圆里的字 (无描边); WmV: 纯矢量色块
        "Style: Wm,Noto Sans SC,%d,&H00FFFFFF,&H00FFFFFF,&H78000000,&H60000000,1,0,0,0,100,100,0,0,1,1,1,7,0,0,0,1" % f_date,
        "Style: WmP,Noto Sans SC,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1" % f_pill,
        "Style: WmV,Noto Sans SC,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1" % f_pill,
        "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def shape(a, b, color, path, alpha="&H00&", layer=1):
        lines.append("Dialogue: %d,%s,%s,WmV,,0,0,0,,{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c%s\\1a%s}%s{\\p0}"
                     % (layer, ts(a), ts(b), color, alpha, path))

    def txt(a, b, style, an, px, py, fs, color, s_text):
        lines.append("Dialogue: 2,%s,%s,%s,,0,0,0,,{\\an%d\\pos(%d,%d)\\fs%d\\1c%s}%s"
                     % (ts(a), ts(b), style, an, int(px), int(py), fs, color, esc(s_text)))

    def txt_raw(a, b, an, px, py, fs, body):
        """body 里允许内联标签 (一行里混字号/颜色), 交给 libass 自己排版"""
        lines.append("Dialogue: 2,%s,%s,Wm,,0,0,0,,{\\an%d\\pos(%d,%d)\\fs%d}%s"
                     % (ts(a), ts(b), an, int(px), int(py), fs, body))

    # 顶部窄条: 与原版一致的 rgba(13,18,26,.15)
    lines.append("Dialogue: 0,0:00:00.00,%s,,WmV,0,0,0,,{\\an7\\pos(0,0)\\bord0\\shad0"
                 "\\1c&H1A120D&\\alpha&HD9&\\p1}%s{\\p0}"
                 % (ts(dur), _ass_pts([(0, 0), (W, 0), (W, bh), (0, bh)])))

    def fr_at(ct):
        lo, hi, ans = 0, len(frames) - 1, frames[0]
        while lo <= hi:
            mid = (lo + hi) >> 1
            if frames[mid][0] <= ct:
                ans = frames[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return ans

    fr = [fr_at(ss + n) for n in range(dur)]

    def spans(keyf):
        i = 0
        while i < dur:
            k = keyf(i)
            j = i
            while j + 1 < dur and keyf(j + 1) == k:
                j += 1
            yield i, j + 1, k
            i = j + 1

    # ---- 左: 日期时间 + 城市·街道 合成一行 (由 libass 顺序排版, 不依赖宽度估算) ----
    for n in range(dur):
        if t0 is not None:
            date_s = (t0 + datetime.timedelta(seconds=ss + n)).strftime("%Y/%m/%d %H:%M:%S")
        else:
            date_s = ""
        seg = []
        if date_s:
            seg.append("{\\1c%s}%s" % (C_WHITE, esc(date_s)))
        if loc:
            seg.append("{\\fs%d\\1c%s}%s" % (f_loc, C_SOFT, esc(loc)))
        if seg:
            txt_raw(n, n + 1, 4, pad, yc, f_date, "  ".join(seg))

    # ---- 右: 油门 / 刹车 / 挡位 / 转向灯 (状态相同的连续秒合并, 减小字幕体积) ----
    for a, b, k in spans(lambda n: (int(round(fr[n][3])), bool(fr[n][5]), fr[n][1], fr[n][6])):
        thr, brk, gear, turn = k
        shape(a, b, C_THR, _ass_rrect(thr_x, yc - ph / 2.0, thr_w, ph, ph / 2.0))
        txt(a, b, "WmP", 5, thr_x + thr_w / 2.0, yc, f_pill, C_WHITE, "%d%%" % thr)
        shape(a, b, C_BRK if brk else C_BRK_OFF, _ass_rrect(brk_x, yc - ph / 2.0, brk_w, ph, ph / 2.0),
              "&H00&" if brk else "&H70&")
        txt(a, b, "WmP", 5, brk_x + brk_w / 2.0, yc, f_pill, C_WHITE if brk else C_SOFT, "刹车")
        shape(a, b, C_GEAR, _ass_circle(gear_cx, yc, gr, 28))
        txt(a, b, "WmP", 5, gear_cx, yc, f_gear, C_WHITE, gear)
        shape(a, b, C_TRI_ON if turn == 2 else C_TRI_OFF,
              _ass_pts([(rtri_x, yc - tri * 0.62), (rtri_x, yc + tri * 0.62), (rtri_x + tri, yc)]))
        shape(a, b, C_TRI_ON if turn == 1 else C_TRI_OFF,
              _ass_pts([(ltri_x + tri, yc - tri * 0.62), (ltri_x + tri, yc + tri * 0.62), (ltri_x, yc)]))

    # ---- 右: AP 方向盘 + 车速 (按"车速 + AP 状态"分组; 数字变宽时方向盘跟着左移, 与网页一致) ----
    for a, b, k in spans(lambda n: (max(0, int(round(fr[n][2]))), int(fr[n][7] or 0) in (1, 2, 3))):
        sp, on = k
        nw = _wm_tw(str(sp), f_num) * 1.04 + 1          # 宁大勿小: 估大只会让方向盘偏左一点
        disc, glyph = _ass_wheel(num_right - nw - wheel_gap - R, yc, R)
        shape(a, b, C_AP_ON if on else C_AP_OFF, disc, "&H00&" if on else "&H61&")
        shape(a, b, C_WHITE if on else C_AP_GLY, glyph, layer=2)
        # 数字右锚(6) / km/h 左锚(4): 位数变化只往左长, 两个元素永远不会互相压叠
        txt(a, b, "Wm", 6, num_right, yc, f_num, C_WHITE, str(sp))
        txt(a, b, "Wm", 4, num_right + int(round(6 * s)), yc, f_unit, C_SOFT, "km/h")

    p = os.path.join(RES_DIR, "wm-%s.ass" % hashlib.md5(
        ("%s|%d|%d|%dx%d|ap7" % (fp, int(ss), dur, W, H)).encode()).hexdigest()[:16])
    with open(p, "w", encoding="utf-8") as fo:
        fo.write("\n".join(lines) + "\n")
    return p


def _make_push_video(fp, off, quality="480p", dur=30):
    """把触发摄像头那一路片段, 从触发点前 5 秒起截 dur 秒, 转成体积可控的 mp4; 返回缓存路径。
    有行车遥测(SEI)且设备上有字体时, 烧录与网页同款的行车信息水印 (手动保存的行车片段即有遥测;
    停车哨兵片段没有, 不烧)。"""
    h, kbps = VID_Q.get(quality) or VID_Q["480p"]
    dur = max(10, min(60, int(dur or 30)))
    ss = max(0.0, float(off) - 5.0)       # 触发点前 5 秒, 能看到"人走过来"的过程
    # 手动保存的片段: 触发点常在片段末尾, "前5秒起截"会只剩几秒。
    # 按实际时长钳制: 起点往前挪, 保证凑满 dur 秒且不越过片段末尾。
    try:
        clip_dur = probe_duration(fp) or 0.0
        if clip_dur > 0:
            dur = max(5, min(dur, int(clip_dur)))
            ss = max(0.0, min(ss, clip_dur - dur))
    except Exception:
        pass
    ass = ""
    try:
        if dur >= 10:
            meta = {}
            try:
                with open(os.path.join(os.path.dirname(fp), "event.json"), encoding="utf-8", errors="ignore") as f:
                    meta = json.load(f) or {}
            except Exception:
                meta = {}
            # 单路是 scale=-2:h, 真实宽度由源宽高比决定 (特斯拉原片 4:3 → 720p 只有 960 宽)。
            # ASS 的 PlayRes 必须等于真实输出尺寸, 否则 x 坐标会被按比例压缩而字号不变。
            sw, sh = probe_size(fp) or (1280, 960)
            ow = max(2, int(round(sw * float(h) / max(1, sh) / 2.0)) * 2)
            ass = _wm_ass(fp, ss, dur, meta, size=(ow, h)) or ""
    except Exception:
        ass = ""
    key = hashlib.md5(("%s|%d|%.2f|%s|%d|%s" % (fp, int(off), ss, quality, dur, "wm3" if ass else "-")).encode()).hexdigest()[:16]
    out = os.path.join(RES_DIR, "push-%s.mp4" % key)
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    if not os.path.isfile(FFMPEG_BIN):
        raise RuntimeError("设备上没有 ffmpeg")
    tmp = out + ".%d.tmp" % os.getpid()
    vf = "scale=-2:%d" % h
    if ass:
        vf += ",subtitles='%s':fontsdir='%s'" % (ass.replace(":", "\\:").replace("'", ""), FONT_DIR.replace("'", ""))
    cmd = ["sh", "-c", 'echo 300 > /proc/self/oom_score_adj 2>/dev/null; exec "$0" "$@"',
           FFMPEG_BIN, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "4",
           "-ss", "%.2f" % ss, "-t", str(dur), "-i", fp,
           "-vf", vf,
           "-c:v", "libx264", "-preset", "superfast",
           "-b:v", "%dk" % kbps, "-maxrate", "%dk" % (kbps * 3 // 2), "-bufsize", "%dk" % (kbps * 3),
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-f", "mp4", "-y", tmp]
    try:
        env = dict(os.environ)
        if ass:
            fc = _wm_fontconf()
            if fc:
                env["FONTCONFIG_FILE"] = fc
        with Q_SEM:        # 与点播转码共用并发闸门: 设备上 ffmpeg 总数恒定, 叠加会触发内核 lockup 重启
            r = subprocess.run(cmd, capture_output=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError("转码超时 (>5 分钟)")
    if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError("转码失败: %s" % (r.stderr or b"")[:100].decode("utf-8", "ignore"))
    os.replace(tmp, out)
    try:
        res_cleanup()          # 推送视频也走同一个缓存目录, 超 2GB 从最旧删
    except Exception:
        pass
    return out


def _transcode_push_upload(src, quality="480p"):
    """把浏览器本机合成上传来的片段 (webm/mp4) 整段转成体积可控的 mp4, 供推飞书;
    返回 (mp4路径, 时长秒)。单路输入, 负载与推送转码同级, 设备可承受。"""
    h, kbps = VID_Q.get(quality) or VID_Q["480p"]
    try:
        st = os.stat(src)
    except OSError:
        raise RuntimeError("上传的片段不存在")
    key = hashlib.md5(("%d|%d|%s" % (st.st_size, int(st.st_mtime), quality)).encode()).hexdigest()[:16]
    out = os.path.join(RES_DIR, "push-manual-%s.mp4" % key)
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out, probe_duration(out) or 0.0
    if not os.path.isfile(FFMPEG_BIN):
        raise RuntimeError("设备上没有 ffmpeg")
    tmp = out + ".%d.tmp" % os.getpid()
    cmd = ["sh", "-c", 'echo 300 > /proc/self/oom_score_adj 2>/dev/null; exec "$0" "$@"',
           FFMPEG_BIN, "-nostdin", "-hide_banner", "-loglevel", "error",
           "-i", src, "-vf", "scale=-2:%d" % h,
           "-c:v", "libx264", "-preset", "superfast",
           "-b:v", "%dk" % kbps, "-maxrate", "%dk" % (kbps * 3 // 2), "-bufsize", "%dk" % (kbps * 3),
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-f", "mp4", "-y", tmp]
    try:
        with Q_SEM:        # 与点播转码共用并发闸门, 防止多路 ffmpeg 叠加触发内核 lockup 重启
            r = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError("转码超时 (>15 分钟)")
    if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError("转码失败: %s" % (r.stderr or b"")[:120].decode("utf-8", "ignore"))
    os.replace(tmp, out)
    try:
        res_cleanup()
    except Exception:
        pass
    return out, probe_duration(out) or 0.0


def manual_push(conf, src, cat, name, quality="480p"):
    """把一个现成的视频文件 (浏览器合成的多路画面) 推到飞书私聊; 返回 (成功?, 提示)。
    src 会被整段转码后上传, 推完发送事件卡片 (带查看按钮)。"""
    if not _dm_ready(conf):
        return False, "飞书私聊未就绪: 需要在「推送」里配好 App ID/Secret + 接收人 (群机器人 webhook 发不了视频)"
    vid, dur = _transcode_push_upload(src, quality)
    mb = os.path.getsize(vid) / 1048576.0
    if mb > 28:
        return False, "转码后仍有 %.0fMB, 超过飞书 30MB 上限; 请选更短片段或更低清晰度" % mb
    fk = _feishu_upload_file(conf, vid, dur)
    _feishu_send_media(conf, fk)
    try:
        _feishu_send_dm(conf, _feishu_card(conf, cat, name, img_key=None))
    except Exception:
        pass
    return True, "已推送 %.0f 秒视频 (%.1fMB) 到飞书私聊" % (dur, mb)


def _feishu_upload_file(conf, path, dur=30):
    """上传视频文件到飞书, 返回 file_key"""
    with open(path, "rb") as f:
        data = f.read()
    bd = "----tsf%s" % hashlib.md5(("%s%.6f" % (path, time.time())).encode()).hexdigest()

    def fld(k, v):
        return ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (bd, k, v)).encode()

    body = (fld("file_type", "mp4") + fld("file_name", os.path.basename(path))
            + fld("duration", str(int(max(1, dur) * 1000)))
            + ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clip.mp4\"\r\n"
               "Content-Type: video/mp4\r\n\r\n" % bd).encode()
            + data + ("\r\n--%s--\r\n" % bd).encode())
    raw = _http_post(FEISHU_API + "/im/v1/files", body, {
        "Authorization": "Bearer " + _feishu_token(conf),
        "Content-Type": "multipart/form-data; boundary=" + bd,
    }, timeout=180)
    j = json.loads(raw.decode("utf-8", "replace"))
    key = ((j or {}).get("data") or {}).get("file_key")
    if j.get("code") != 0 or not key:
        raise RuntimeError("上传视频失败: %s" % (j.get("msg") or j))
    return key


def _feishu_send_media(conf, file_key, image_key=""):
    """给本人发一条视频消息 (media)"""
    rid, rtype = _dm_target(conf)
    content = {"file_key": file_key}
    if image_key:
        content["image_key"] = image_key
    j = _post_json(FEISHU_API + "/im/v1/messages?receive_id_type=" + urllib.parse.quote(rtype),
                   {"receive_id": rid, "msg_type": "media", "content": json.dumps(content)},
                   timeout=60, headers={"Authorization": "Bearer " + _feishu_token(conf)})
    if j.get("code") != 0:
        raise RuntimeError("%s (code=%s)" % (j.get("msg") or "发视频失败", j.get("code")))
    return True


def _feishu_card(conf, cat, name, img_key=None, test=False):
    """拼一张飞书卡片 (标题/时间/位置/原因/镜头/封面/查看按钮)"""
    d = os.path.join(ARCHIVE, cat, name)
    meta = {}
    try:
        with open(os.path.join(d, "event.json"), encoding="utf-8", errors="ignore") as f:
            meta = json.load(f) or {}
    except Exception:
        pass
    cat_name, color, detail = _reason_info(meta.get("reason"))
    if cat == "SavedClips" and not str(meta.get("reason", "")).startswith("sentry_"):
        cat_name, color = "手动保存", "grey"
    cam = CAM_NUM.get(int(meta["camera"])) if str(meta.get("camera", "")).strip().isdigit() else None
    when = str(meta.get("timestamp") or name)[:19].replace("T", " ").replace("_", " ")
    try:
        dt = datetime.datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
        when = "%d月%d日 %02d:%02d:%02d" % (dt.month, dt.day, dt.hour, dt.minute, dt.second)
    except Exception:
        pass
    loc = " ".join(x for x in (meta.get("city") or "", meta.get("street") or "") if x).strip() or "位置未知"
    nclip = len(_ev_files(d))
    title = ("🧪 [测试] " if test else "🚨 ") + cat_name + (" · " + cam if cam else "")
    fields = [("时间", when), ("位置", loc), ("触发原因", detail)]
    if cam:
        fields.append(("触发镜头", "%s (#%s)" % (cam, meta.get("camera"))))
    fields.append(("片段", "%d 路画面" % nclip))
    els = []
    if img_key:
        els.append({"tag": "img", "img_key": img_key, "alt": {"tag": "plain_text", "content": "触发画面"}})
    for i in range(0, len(fields), 2):
        els.append({"tag": "div", "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": "**%s**\n%s" % (k, v)}}
            for k, v in fields[i:i + 2]]})
    link = (conf.get("public_url") or "").rstrip("/")
    acts = []
    if link:
        acts.append({"tag": "button", "text": {"tag": "plain_text", "content": "查看录像"},
                     "url": "%s/?ev=%s/%s" % (link, urllib.parse.quote(cat), urllib.parse.quote(name)),
                     "type": "primary"})
    if acts:
        els.append({"tag": "action", "actions": acts})
    if test:
        els.append({"tag": "note", "elements": [{"tag": "plain_text", "content": "这是一条测试推送"}]})
    return {
        "config": {"wide_screen": True},
        "header": {"template": color, "title": {"tag": "plain_text", "content": title}},
        "elements": els,
    }


def _dm_target(conf):
    """返回 (receive_id, receive_id_type); 没配返回 (None, None)。
    自动识别: ou_/on_ → open_id, oc_ → chat_id, 含 @ → email"""
    rid = str(conf.get("receive_id") or "").strip()
    if not rid:
        return None, None
    t = str(conf.get("receive_id_type") or "").strip()
    if not t:
        if rid.startswith("oc_"):
            t = "chat_id"
        elif "@" in rid:
            t = "email"
        else:
            t = "open_id"
    return rid, t


def _dm_ready(conf):
    rid, _t = _dm_target(conf)
    return bool(rid and conf.get("app_id") and conf.get("app_secret"))


def _feishu_send_dm(conf, card):
    """用自建应用机器人给指定用户发卡片 — 只进你和机器人的单聊, 别人看不到"""
    rid, rtype = _dm_target(conf)
    url = FEISHU_API + "/im/v1/messages?receive_id_type=" + urllib.parse.quote(rtype)
    payload = {"receive_id": rid, "msg_type": "interactive",
               "content": json.dumps(card, ensure_ascii=False)}
    j = _post_json(url, payload, timeout=20,
                   headers={"Authorization": "Bearer " + _feishu_token(conf)})
    if j.get("code") != 0:
        raise RuntimeError("%s (code=%s)" % (j.get("msg") or "发送失败", j.get("code")))
    return True


def notify_push(cat, name, test=False):
    """推一条到飞书; 返回 (成功?, 提示)"""
    conf = notify_conf()
    dm = _dm_ready(conf)
    hook = str(conf.get("webhook") or "").strip()
    use_dm = dm and (conf.get("mode") != "group" or not hook)
    if not use_dm and not hook:
        return False, "未配置: 需要 App ID/Secret + 接收人 (私聊), 或群机器人 webhook"
    img_key = None
    warn = ""
    if conf.get("app_id") and conf.get("app_secret"):
        try:
            jpg, _m = _trigger_frame(cat, name)
            if jpg:
                img_key = _feishu_upload(conf, jpg)
            else:
                warn = " (没抽到封面帧)"
        except Exception as ex:
            warn = " (封面未附上: %s)" % str(ex)[:80]
    else:
        warn = " (未配 app_id/app_secret, 只发文字卡片)"
    # 视频模式: 先把触发摄像头那一路上传成视频消息, 再补一条精简卡片(带查看按钮)
    if use_dm and (conf.get("media") or "thumb") == "video":
        try:
            fp, off, _meta = _pick_clip(cat, name)
            if not fp:
                raise RuntimeError("归档里没有可用的片段")
            dur = int(conf.get("vdur") or 30)
            vid = _make_push_video(fp, off, conf.get("quality") or "480p", dur)
            fk = _feishu_upload_file(conf, vid, dur)
            _feishu_send_media(conf, fk, img_key or "")
            try:
                _feishu_send_dm(conf, _feishu_card(conf, cat, name, img_key=None, test=test))
            except Exception:
                pass
            mb = os.path.getsize(vid) / 1048576.0
            return True, "已推送 %d 秒视频 (%.1fMB) 到飞书私聊" % (dur, mb) + warn
        except Exception as ex:
            warn = " (视频没发成, 已回落封面卡片: %s)" % str(ex)[:90]
    card = _feishu_card(conf, cat, name, img_key=img_key, test=test)
    try:
        if use_dm:
            _feishu_send_dm(conf, card)
            return True, "已推送到你的飞书私聊" + warn
        j = _post_json(hook, {"msg_type": "interactive", "card": card})
        code = j.get("code", j.get("StatusCode", -1))
        if code not in (0, None):
            raise RuntimeError("%s (code=%s)" % (j.get("msg") or j.get("StatusMessage") or "发送失败", code))
    except Exception as ex:
        return False, "推送失败: %s" % str(ex)[:140]
    return True, "已推送到飞书群" + warn


def _ev_ts(name):
    """从事件目录名 YYYY-MM-DD_HH-MM-SS 解析事件真实发生时间; 失败返回 0"""
    try:
        return time.mktime(time.strptime(name, "%Y-%m-%d_%H-%M-%S"))
    except Exception:
        return 0


def notify_scan():
    """扫一遍归档, 把新出现的哨兵事件推给飞书; 返回本轮推送条数"""
    conf = notify_conf()
    if not conf.get("enabled") or not (_dm_ready(conf) or str(conf.get("webhook") or "").strip()):
        return 0
    st = _nstate_load()
    seen = st.setdefault("seen", {})
    tries = st.setdefault("try", {})
    first = not st.get("init")
    delay = max(5, int(conf.get("delay") or 20))
    cats = ["SentryClips"] + (["SavedClips"] if conf.get("saved") else [])
    sent = 0
    for cat in cats:
        if sent >= 3:
            break
        base = os.path.join(ARCHIVE, cat)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", name):
                continue
            key = cat + "/" + name
            if key in seen:
                continue
            d = os.path.join(base, name)
            newest = 0.0
            try:
                for f in os.listdir(d):
                    newest = max(newest, os.path.getmtime(os.path.join(d, f)))
            except OSError:
                continue
            now = time.time()
            if now - newest < delay:
                continue                     # 还在同步写入, 下一轮再看
            # 两道判定:
            # (1) 目录名里的日期 = 事件真实发生时间(车机时钟)。车机U盘循环覆盖后
            #     tesla-sync 会把旧片段重新归档, 复制出来的文件 mtime 是"复制时刻",
            #     只看 mtime 会把几天前的事件当新事件推出去。所以超过 24h 的直接记账不推
            #     (留 24h 余量足以吸收车机与盒子的时区偏差)。
            # (2) 新鲜度仍按 mtime: 保留"还在写入就不推""超过 6h 不补"的原语义。
            ev_ts = _ev_ts(name)
            if ev_ts and now - ev_ts > 24 * 3600:
                seen[key] = int(now)         # 历史回灌(一天以前的片段), 不推
                continue
            if first or now - newest > 6 * 3600:
                seen[key] = int(now)         # 首次启用不补历史; 太旧的(>6h)也不补
                continue
            ok, msg = notify_push(cat, name)
            st["last"] = {"at": int(time.time()), "ok": ok, "msg": msg, "ev": key}
            if ok:
                sent += 1
                seen[key] = int(now)
                tries.pop(key, None)
            else:
                tries[key] = tries.get(key, 0) + 1
                if tries[key] >= 3:
                    seen[key] = int(now)      # 连推 3 次都失败就放弃, 防止卡住队列
                    tries.pop(key, None)
            if sent >= 3:
                break
    if first:
        st["init"] = 1
    if len(seen) > 3000:                       # 防止状态文件无限膨胀
        for k in sorted(seen, key=lambda x: seen.get(x) or 0)[:len(seen) - 2000]:
            seen.pop(k, None)
    _nstate_save(st)
    return sent


def notify_loop():
    time.sleep(25)
    while True:
        try:
            notify_scan()
        except Exception:
            pass
        time.sleep(30)


def notify_status():
    conf = notify_conf()
    st = _nstate_load()
    rid, rtype = _dm_target(conf)
    return {
        "enabled": bool(conf.get("enabled")),
        "mode": conf.get("mode") or "dm",
        "webhook": conf.get("webhook") or "",
        "app_id": conf.get("app_id") or "",
        "has_secret": bool(conf.get("app_secret")),
        "receive_id": rid or "",
        "receive_id_type": rtype or "",
        "dm_ready": _dm_ready(conf),
        "public_url": conf.get("public_url") or "",
        "saved": bool(conf.get("saved")),
        "delay": int(conf.get("delay") or 20),
        "media": conf.get("media") or "thumb",
        "quality": conf.get("quality") or "480p",
        "vdur": int(conf.get("vdur") or 30),
        "last": st.get("last") or {},
        "sent_total": len(st.get("seen") or {}),
    }


def get_variant(fp, qv):
    """返回 qv 清晰度的缓存文件路径; 没有就排队转码一份 (最多2个并发), 失败回退原文件"""
    try:
        st = os.stat(fp)
    except OSError:
        return fp
    key = hashlib.md5(("%s|%d|%d|%s" % (fp, st.st_size, st.st_mtime, qv)).encode()).hexdigest()
    out = os.path.join(RES_DIR, key + ".mp4")
    if os.path.isfile(out) and os.path.getsize(out) > 0:
        return out
    if not os.path.isfile(FFMPEG_BIN):
        return fp
    # 排队等空闲转码槽 (最多 10 分钟, 超时回退原文件)
    if not Q_SEM.acquire(timeout=600):
        return fp
    try:
        if os.path.isfile(out) and os.path.getsize(out) > 0:  # 别人刚转完
            return out
        try:  # 内存保护: 可用 <180MB 不转, 回退原文件
            with open("/proc/meminfo") as mf:
                for line in mf:
                    if line.startswith("MemAvailable"):
                        if int(line.split()[1]) < 180000:
                            return fp
                        break
        except Exception:
            pass
        w, h, kbps = RES_SCALE[qv]
        tmp = out + ".%d.tmp" % os.getpid()
        # 子进程把 oom_score_adj 调回正数 (父进程是 -300 会被继承), OOM 时优先杀转码而不是主服务
        cmd = ["sh", "-c",
               'echo 300 > /proc/self/oom_score_adj 2>/dev/null; exec "$0" "$@"',
               FFMPEG_BIN, "-nostdin", "-hide_banner", "-loglevel", "error",
               "-threads", "4", "-i", fp,
               "-vf", "scale=%d:%d" % (w, h),
               "-c:v", "libx264", "-preset", "superfast",
               "-b:v", "%dk" % kbps, "-maxrate", "%dk" % (kbps * 3 // 2),
               "-bufsize", "%dk" % (kbps * 3),
               "-c:a", "copy", "-movflags", "+faststart", "-f", "mp4", "-y", tmp]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=900)
            if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, out)
                res_cleanup()
                return out
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return fp
    finally:
        Q_SEM.release()
CAM_ORDER6 = ["left_pillar", "front", "right_pillar", "left_repeater", "back", "right_repeater"]
CAM_ORDER4 = ["front", "back", "left_repeater", "right_repeater"]


def cam_of(f):
    return f.rsplit("-", 1)[-1][:-4] if "-" in f else f


def clip_key(f):
    """文件名里的片段组标识: '2026-11-01_12-41-49-front.mp4' -> '2026-11-01_12-41-49'
    一个事件目录里常有 10 分钟的多组片段 (每组 4-6 路), 需要按组区分"""
    base = f[:-4] if f.lower().endswith(".mp4") else f
    return base.rsplit("-", 1)[0] if "-" in base else base


def latest_group(names):
    """取时间戳最新的一组片段 (与事件时间最接近的一组)"""
    if not names:
        return []
    groups = {}
    for f in names:
        groups.setdefault(clip_key(f), []).append(f)
    return groups[max(groups)]


def order_files(names):
    """按方位顺序排列 mp4: 6 路左右分列/前后居中, 4 路前后一排、左右B柱一排 (与前端 camSort 一致)"""
    order = CAM_ORDER6 if len(names) >= 6 else CAM_ORDER4
    return sorted(names, key=lambda f: (order.index(cam_of(f)) if cam_of(f) in order else 99, f))


def probe_duration(path):
    try:
        out = subprocess.run([FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, timeout=20).stdout.decode().strip()
        return float(out)
    except Exception:
        return 0.0


def probe_size(path):
    """返回视频 (宽, 高); 失败返回 None。水印 ASS 的 PlayResX/PlayResY 必须等于输出尺寸,
    否则 libass 会按 帧宽/PlayResX 压缩 x 坐标而字号不变, 元素之间会互相压叠。"""
    try:
        out = subprocess.run([FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=width,height",
                              "-of", "csv=s=x:p=0", path],
                             capture_output=True, timeout=20).stdout.decode().strip()
        w, h = out.replace("\r", "").splitlines()[0].split("x")[:2]
        return int(w), int(h)
    except Exception:
        return None


def mem_available_mb():
    """读取 MemAvailable, 用于合成前的内存兜底检查"""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def _stitch_worker(job_id, paths, out_path, total):
    n = len(paths)
    cols = 1 if n == 1 else (2 if n <= 4 else 3)
    # 每格分辨率: 默认 640x360 (4 路整幅 1280x720)。设备只有 1.4G 内存,
    # 输出 1080p 时 4 路 1080p 解码 + x264 帧缓存会把内存吃光导致整机失联。
    # 需要更高画质时用环境变量 TSF_CELL=800x450 之类覆盖。
    cell = os.environ.get("TSF_CELL", "").lower()
    if "x" in cell:
        try:
            W, H = [int(x) for x in cell.split("x", 1)]
        except ValueError:
            W, H = 640, 360
    elif n == 2:
        W, H = 960, 540      # 两路时整幅 1920x540, 负载可控
    else:
        W, H = 640, 360
    cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
           "-filter_threads", "1"]
    for f in paths:
        cmd += ["-i", f]
    fc = []
    for i in range(n):
        fc.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                  f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=15[v{i}]")
    layout = "|".join("%d_%d" % ((i % cols) * W, (i // cols) * H) for i in range(n))
    fc.append("".join(f"[v{i}]" for i in range(n)) + f"xstack=inputs={n}:layout={layout}:fill=black[vout]")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-threads", "2", "-x264-params", "ref=2:bframes=0",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", out_path]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # 万一仍然吃爆内存, 让内核优先杀 ffmpeg, 保住 init/viewer 不被 OOM 带走
        try:
            with open("/proc/%d/oom_score_adj" % proc.pid, "w") as f:
                f.write("800")
        except OSError:
            pass
        deadline = time.time() + max(600, total * 30)  # 超时保护, 防止任务永久卡住
        timed_out = False
        for raw in proc.stdout:
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("out_time_us=") and total > 0:
                try:
                    done = int(line.split("=", 1)[1]) / 1000000.0
                    pct = max(0, min(99, int(done / total * 100)))
                    with STITCH_LOCK:
                        if job_id in STITCH_JOBS:
                            STITCH_JOBS[job_id]["pct"] = pct
                except ValueError:
                    pass
            if time.time() > deadline:
                timed_out = True
                try:
                    proc.kill()
                except OSError:
                    pass
                break
        err = proc.stderr.read().decode("utf-8", "ignore")
        code = proc.wait()
        with STITCH_LOCK:
            job = STITCH_JOBS.get(job_id)
            if not job:
                return
            if timed_out:
                job["state"] = "error"
                job["err"] = "合成超时(超过 %d 分钟), 请少选几路再试" % int(max(600, total * 30) // 60)
                return
            if code == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
                job["state"] = "done"
                job["pct"] = 100
                job["path"] = out_path
                job["size"] = os.path.getsize(out_path)
            else:
                job["state"] = "error"
                tail = [l for l in (err or "").strip().splitlines() if l.strip()]
                job["err"] = (tail[-1] if tail else "ffmpeg 退出码 %d" % code)[:300]
    except Exception as e:
        with STITCH_LOCK:
            job = STITCH_JOBS.get(job_id)
            if job:
                job["state"] = "error"
                job["err"] = str(e)[:300]


def start_stitch(cat, name, cams):
    """创建合成任务, 返回 (job_id, 错误信息)"""
    # 本机 (F50) 整个系统跑在 loop 镜像上, 实测 ffmpeg 多路 1080p 解码+编码会触发
    # 内核 soft/hard lockup -> panic=5 自动重启, 已用 2/4 路真实素材复现两次。
    # 前端默认走浏览器本机合成, 这里仅保留 TSF_STITCH=1 的实验开关。
    if os.environ.get("TSF_STITCH", "0") != "1":
        return None, "设备端合成已停用(该负载会触发设备内核重启), 请使用浏览器合成或打包下载"
    if not os.path.isfile(FFMPEG_BIN):
        return None, "设备上未安装 ffmpeg (预期 %s)" % FFMPEG_BIN
    evdir = safe_archive_dir(os.path.join(ARCHIVE, cat, name))
    if not evdir or not os.path.isdir(evdir):
        return None, "事件不存在"
    files = [f for f in os.listdir(evdir) if f.lower().endswith(".mp4")]
    files = latest_group(files)  # 只取最新一组片段, 24 个片段全拼会直接压垮设备
    if cams:
        files = [f for f in files if cam_of(f) in cams]
    files = order_files(files)
    if len(files) < 2:
        return None, "至少需要选择两路画面"
    paths = [os.path.join(evdir, f) for f in files]
    os.makedirs(STITCH_DIR, exist_ok=True)
    # 内存与并发保护: 设备只有 1.4G, 同时跑两个合成会直接 OOM 拖垮整机
    with STITCH_LOCK:
        running = [j for j in STITCH_JOBS.values() if j.get("state") == "running"]
    if running:
        return None, "已有合成任务在进行, 请等待完成后再试"
    avail = mem_available_mb()
    if 0 < avail < 300:
        return None, "设备可用内存不足(%dMB), 暂停合成以免影响录像同步" % avail
    now = time.time()
    for old_f in os.listdir(STITCH_DIR):
        fp_old = os.path.join(STITCH_DIR, old_f)
        try:
            if os.path.isfile(fp_old) and now - os.path.getmtime(fp_old) > 7200:
                os.unlink(fp_old)
        except OSError:
            pass
    job_id = "%s-%d" % (name.replace(":", "-"), int(time.time() * 1000) % 1000000)
    out_path = os.path.join(STITCH_DIR, job_id + ".mp4")
    total = probe_duration(paths[0])
    with STITCH_LOCK:
        STITCH_JOBS[job_id] = {"state": "running", "pct": 0, "path": None,
                               "name": "%s_%d路.mp4" % (name, len(files)), "total": total}
    threading.Thread(target=_stitch_worker, args=(job_id, paths, out_path, total), daemon=True).start()
    return job_id, None


# ---------- 服务端"带水印导出": 给 iOS / 微信 一份标准 H.264 mp4 ----------
# 为什么不用浏览器合成: iOS Safari 的 MediaRecorder 只认 video/webm (微信发不出去),
# iOS 26 起录 mp4 还会被截断(实测只留前几秒)。所以苹果设备改由设备端 ffmpeg 出片。
# 负载控制: 多路时先逐路单独降采样成小文件(同时只解 1 路 1080p), 再拼小文件,
# 避免 2~4 路 1080p 同时解码触发内核 lockup。
EXPORT_Q = {"720p": 720, "480p": 480}


def _ffrun(cmd, timeout=900):
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as ex:
        return -1, str(ex)
    try:
        with open("/proc/%d/oom_score_adj" % p.pid, "w") as f:
            f.write("800")            # 内存吃紧时先杀 ffmpeg, 保住主服务
    except OSError:
        pass
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            p.kill()
        except OSError:
            pass
        return -1, "转码超时"
    return p.returncode, (err or b"").decode("utf-8", "ignore")[-300:]


def _export_worker(job_id, paths, out_path, total, wm, height):
    n = len(paths)
    tmpdir = os.path.join(STITCH_DIR, "ex-" + job_id.replace("/", "_"))
    os.makedirs(tmpdir, exist_ok=True)

    def setpct(v):
        with STITCH_LOCK:
            job = STITCH_JOBS.get(job_id)
            if job:
                job["pct"] = max(0, min(99, int(v)))

    def fail(msg):
        with STITCH_LOCK:
            job = STITCH_JOBS.get(job_id)
            if job:
                job["state"] = "error"
                job["err"] = (msg or "ffmpeg 失败")[:300]

    try:
        # 画面尺寸: 单路保持原比例; 多路统一 2 列网格, 输出仍是 16:9 便于水印排版
        if n == 1:
            OH = height
            # 单路宽度由源宽高比决定 (特斯拉原片是 1280x960 的 4:3, 720p 下实际输出 960x720)。
            # 水印 ASS 的 PlayRes 必须用这个真实宽度, 不能写死 1280, 否则 x 坐标被压 0.75 倍。
            sw, sh = probe_size(paths[0]) or (1280, 960)
            OW = max(2, int(round(sw * float(OH) / max(1, sh) / 2.0)) * 2)
        else:
            cols, rows = 2, (n + 1) // 2
            OW, OH = 1280, 720
            CW, CH = OW // cols, OH // rows

        # 第一步: 逐路降采样 (顺序执行, 峰值只有 1 路 1080p 解码)
        small = []
        if n > 1:
            for i, fp in enumerate(paths):
                sp = os.path.join(tmpdir, "c%d.mp4" % i)
                vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                      "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=15" % (CW, CH, CW, CH))
                code, err = _ffrun([FFMPEG_BIN, "-nostdin", "-hide_banner", "-loglevel", "error",
                                    "-threads", "2", "-i", fp, "-vf", vf, "-an",
                                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                                    "-threads", "2", "-y", sp], timeout=600)
                if code != 0 or not os.path.isfile(sp):
                    fail(err or ("第 %d 路降采样失败" % (i + 1)))
                    return
                small.append(sp)
                setpct((i + 1) / float(n) * 70)
        else:
            small = paths

        ass = ""
        if wm:
            try:
                meta = {}
                try:
                    with open(os.path.join(os.path.dirname(paths[0]), "event.json"),
                              encoding="utf-8", errors="ignore") as f:
                        meta = json.load(f) or {}
                except Exception:
                    meta = {}
                ass = _wm_ass(paths[0], 0, probe_duration(paths[0]) or 0, meta,
                              size=(OW, OH)) or ""
            except Exception:
                ass = ""

        cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y", "-filter_threads", "1"]
        for f in small:
            cmd += ["-i", f]
        fc = []
        if n == 1:
            fc.append("[0:v]scale=-2:%d,setsar=1,fps=15[v0]" % height)
            last = "[v0]"
        else:
            for i in range(n):
                fc.append("[%d:v]setsar=1[v%d]" % (i, i))
            cells = "|".join("%d_%d" % ((i % 2) * CW, (i // 2) * CH) for i in range(n))
            fc.append("".join("[v%d]" % i for i in range(n)) +
                      "xstack=inputs=%d:layout=%s:fill=black[vg]" % (n, cells))
            last = "[vg]"
        if ass:
            sub = "subtitles='%s':fontsdir='%s'" % (ass.replace(":", "\\:").replace("'", ""),
                                                    FONT_DIR.replace("'", ""))
            fc.append("%s%s[vout]" % (last, sub))
            last = "[vout]"
        cmd += ["-filter_complex", ";".join(fc), "-map", last, "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p",
                "-threads", "2", "-c:a", "aac", "-b:a", "96k",
                "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", out_path]
        env = dict(os.environ)
        if ass:
            fcnf = _wm_fontconf()
            if fcnf:
                env["FONTCONFIG_FILE"] = fcnf
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            with open("/proc/%d/oom_score_adj" % p.pid, "w") as f:
                f.write("800")
        except OSError:
            pass
        deadline = time.time() + max(600, (total or 60) * 40)
        for raw in p.stdout:
            line = raw.decode("utf-8", "ignore").strip()
            if line.startswith("out_time_us=") and total:
                try:
                    done = int(line.split("=", 1)[1]) / 1000000.0
                    setpct(70 + min(29, done / float(total) * 29))
                except ValueError:
                    pass
            if time.time() > deadline:
                try:
                    p.kill()
                except OSError:
                    pass
                break
        err = p.stderr.read().decode("utf-8", "ignore")
        code = p.wait()
        if code == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            with STITCH_LOCK:
                job = STITCH_JOBS.get(job_id)
                if job:
                    job["state"] = "done"
                    job["pct"] = 100
                    job["path"] = out_path
                    job["size"] = os.path.getsize(out_path)
        else:
            tail = [x for x in (err or "").strip().splitlines() if x.strip()]
            fail(tail[-1] if tail else ("ffmpeg 退出码 %d" % code))
    except Exception as ex:
        fail(str(ex))
    finally:
        try:
            for f in os.listdir(tmpdir):
                os.unlink(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except OSError:
            pass


def start_export(cat, name, cams, wm=True, q="720p", files=None):
    """创建"服务端带水印导出"任务, 返回 (job_id, 错误)
    files: 前端指定要导出的具体片段文件名 (用户正在看的那一分钟),
           不传则退回"最新一组"的行为。"""
    if not os.path.isfile(FFMPEG_BIN):
        return None, "设备上没有 ffmpeg, 无法生成 mp4"
    evdir = safe_archive_dir(os.path.join(ARCHIVE, cat, name))
    if not evdir or not os.path.isdir(evdir):
        return None, "事件不存在"
    if isinstance(files, list) and files:
        picked = []
        for f in files:
            b = safe_name(str(f))
            if not b or not b.lower().endswith(".mp4"):
                continue
            if not os.path.isfile(os.path.join(evdir, b)):
                continue
            if cams and cam_of(b) not in cams:
                continue
            if b not in picked:
                picked.append(b)
        files = order_files(picked) if picked else []
    else:
        files = [f for f in os.listdir(evdir) if f.lower().endswith(".mp4")]
        files = latest_group(files)
        if cams:
            files = [f for f in files if cam_of(f) in cams]
        files = order_files(files)
    if not files:
        return None, "没有可导出的片段"
    with STITCH_LOCK:
        if any(j.get("state") == "running" for j in STITCH_JOBS.values()):
            return None, "已有导出/合成任务在进行, 请等它完成"
    avail = mem_available_mb()
    if 0 < avail < 260:
        return None, "设备可用内存不足(%dMB), 稍后再试" % avail
    paths = [os.path.join(evdir, f) for f in files]
    os.makedirs(STITCH_DIR, exist_ok=True)
    now = time.time()
    for old in os.listdir(STITCH_DIR):
        p = os.path.join(STITCH_DIR, old)
        try:
            if os.path.isfile(p) and now - os.path.getmtime(p) > 3600:
                os.unlink(p)
        except OSError:
            pass
    job_id = "%s-ex%d" % (name.replace(":", "-"), int(now * 1000) % 1000000)
    out_path = os.path.join(STITCH_DIR, job_id + ".mp4")
    total = probe_duration(paths[0]) or 60
    height = EXPORT_Q.get(q, 720)
    with STITCH_LOCK:
        STITCH_JOBS[job_id] = {"state": "running", "pct": 0, "path": None, "size": 0,
                               "name": "%s_%d路.mp4" % (name, len(files))}
    threading.Thread(target=_export_worker,
                     args=(job_id, paths, out_path, total, bool(wm), height), daemon=True).start()
    return job_id, None


# ---------- 行车遥测: 解码视频 SEI 里的 SeiMetadata protobuf ----------
# Tesla 在车辆行驶中录制的片段里, 每帧塞一条 SEI(type 6) NAL,
# payload 以 magic 0x42 0x42 0x69 开头, 后跟 protobuf:
#   1 version, 2 gear(0P/1D/2R/3N), 3 frame_seq, 4 speed_mps, 5 油门%,
#   6 方向盘角度, 7/8 左右转向灯, 9 刹车, 10 AP状态, 11/12 经纬度,
#   13 航向, 14/15/16 三轴加速度
TEL_DIR = os.environ.get("TSF_CACHE", "/opt/mu300") + "/tel-cache"
TEL_SEM = threading.BoundedSemaphore(int(os.environ.get("TSF_TEL_CONN", "1")))


def _pb_varint(b, i):
    r = 0
    s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, i
        s += 7


def _pb_decode(b):
    out = {}
    i = 0
    n = len(b)
    while i < n:
        try:
            key, i = _pb_varint(b, i)
        except (IndexError, ValueError):
            break
        fn, wt = key >> 3, key & 7
        try:
            if wt == 0:
                out[fn], i = _pb_varint(b, i)
            elif wt == 5:
                out[fn] = struct.unpack("<f", b[i:i + 4])[0]
                i += 4
            elif wt == 1:
                out[fn] = struct.unpack("<d", b[i:i + 8])[0]
                i += 8
            elif wt == 2:
                ln, j = _pb_varint(b, i)
                i = j + ln
            else:
                return None
        except (IndexError, struct.error, ValueError):
            return None
    return out if i == n else None


def _strip_emulation(b):
    out = bytearray()
    zeros = 0
    for x in b:
        if zeros >= 2 and x == 3:
            zeros = 0
            continue
        out.append(x)
        zeros = zeros + 1 if x == 0 else 0
    return bytes(out)


def _mp4_video_samples(fp, max_samples=4000):
    """解析 moov, 返回 [(offset, size), ...] 与 lenSize; 失败返回 None"""
    with open(fp, "rb") as f:
        d = f.read(4096)
        f.seek(0, 2)
        fsize = f.tell()

    def boxes(buf, start, end):
        out = []
        i = start
        while i + 8 <= end:
            if i + 8 > len(buf) and len(buf) < fsize:
                return None  # 需要 moov 全量, 由调用方重新读
            size = struct.unpack(">I", buf[i:i + 4])[0]
            typ = buf[i + 4:i + 8]
            hdr = 8
            if size == 1:
                size = struct.unpack(">Q", buf[i + 8:i + 16])[0]
                hdr = 16
            elif size == 0:
                size = end - i
            if size < hdr:
                break
            out.append((typ, i + hdr, i + size))
            i += size
        return out

    # moov 可能在文件尾, 先定位
    with open(fp, "rb") as f:
        pos = 0
        moov_rng = None
        while pos + 8 <= fsize:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            size = struct.unpack(">I", hdr[:4])[0]
            typ = hdr[4:8]
            hsz = 8
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
                hsz = 16
            elif size == 0:
                size = fsize - pos
            if size < hsz:
                break
            if typ == b"moov":
                moov_rng = (pos + hsz, pos + size)
                break
            pos += size
    if not moov_rng:
        return None
    with open(fp, "rb") as f:
        f.seek(moov_rng[0])
        moov = f.read(moov_rng[1] - moov_rng[0])
    mv_s, mv_e = 0, len(moov)

    def find1(name, s, e):
        for typ, cs, ce in boxes(moov, s, e):
            if typ == name:
                return cs, ce
        return None

    stbl = None
    for typ, ts, te in boxes(moov, mv_s, mv_e):
        if typ != b"trak":
            continue
        r = find1(b"mdia", ts, te)
        if not r:
            continue
        r2 = find1(b"minf", r[0], r[1])
        if not r2:
            continue
        r3 = find1(b"stbl", r2[0], r2[1])
        if not r3:
            continue
        stsd = find1(b"stsd", r3[0], r3[1])
        if stsd and moov.find(b"avcC", stsd[0], stsd[1]) > 0:
            stbl = r3
            break
    if not stbl:
        return None
    s, e = stbl
    stsd = find1(b"stsd", s, e)
    avc = moov.find(b"avcC", stsd[0], stsd[1])
    len_size = (moov[avc + 8] & 3) + 1
    a = find1(b"stsz", s, e)[0]
    uniform = struct.unpack(">I", moov[a + 4:a + 8])[0]
    n = struct.unpack(">I", moov[a + 8:a + 12])[0]
    if uniform:
        sizes = [uniform] * n
        buf = b""
    else:
        take = min(n, max_samples * 2)
        sizes = list(struct.unpack(">%dI" % take, moov[a + 12:a + 12 + 4 * take]))
    ca = find1(b"stco", s, e)[0]
    nch = struct.unpack(">I", moov[ca + 4:ca + 8])[0]
    chunks = struct.unpack(">%dI" % nch, moov[ca + 8:ca + 8 + 4 * nch])
    sa = find1(b"stsc", s, e)[0]
    ns = struct.unpack(">I", moov[sa + 4:sa + 8])[0]
    stsc = struct.unpack(">%dIII" % ns, moov[sa + 8:sa + 8 + 12 * ns])
    offs = []
    si = 0
    for ci in range(nch):
        run = None
        for k in range(ns):
            if stsc[k * 3] - 1 <= ci:
                run = stsc[k * 3:k * 3 + 3]
        per = run[1] if run else 1
        o = chunks[ci]
        for _ in range(per):
            if si >= len(sizes) or len(offs) >= max_samples:
                break
            offs.append((o, sizes[si]))
            o += sizes[si]
            si += 1
    return offs, len_size


def parse_telemetry(fp, stride=3):
    """从 mp4 SEI 提取遥测, 返回 dict 或 None(无SEI)"""
    r = _mp4_video_samples(fp)
    if not r:
        return None
    offs, len_size = r
    dur = probe_duration(fp)
    if not dur or len(offs) < 2:
        return None
    fps = len(offs) / float(dur)
    fsize = os.path.getsize(fp)
    frames = []
    gear_map = {0: "P", 1: "D", 2: "R", 3: "N"}
    with open(fp, "rb") as f, TEL_SEM:
        for k in range(0, len(offs), stride):
            o, ln = offs[k]
            if o + ln > fsize:
                continue
            f.seek(o)
            data = f.read(ln)
            pos = 0
            while pos + len_size <= len(data):
                n2 = int.from_bytes(data[pos:pos + len_size], "big")
                if n2 <= 0 or pos + len_size + n2 > len(data):
                    break
                nal = data[pos + len_size:pos + len_size + n2]
                pos += len_size + n2
                if nal[0] & 0x1F != 6:
                    continue
                # 找 magic: 连续的 0x42 之后遇到 0x69
                pi = None
                for i in range(3, min(len(nal), 100)):
                    if nal[i] == 0x42:
                        continue
                    if nal[i] == 0x69:
                        pi = i + 1
                    break
                if pi is None:
                    continue
                payload = _strip_emulation(nal[pi:len(nal) - 1])
                msg = _pb_decode(payload)
                if not msg:
                    continue
                spd = msg.get(4, 0.0) * 3.6
                frames.append([
                    round(k / fps, 2),
                    gear_map.get(msg.get(2, 1), "D"),
                    round(spd, 1),
                    int(round(msg.get(5, 0.0))),
                    round(msg.get(6, 0.0), 1),
                    1 if msg.get(9) else 0,
                    1 if msg.get(7) else (2 if msg.get(8) else 0),
                    int(msg.get(10, 0)),
                    round(msg.get(11, 0.0), 6),
                    round(msg.get(12, 0.0), 6),
                    round(msg.get(13, 0.0), 1),
                    round(msg.get(14, 0.0), 2),
                    round(msg.get(15, 0.0), 2),
                    round(msg.get(16, 0.0), 2),
                ])
    if not frames:
        return None
    speeds = [f[2] for f in frames]
    brakes = sum(1 for f in frames if f[5])
    return {
        "fps": round(fps, 2),
        "count": len(offs),
        "duration": round(dur, 2),
        "max_speed": max(speeds),
        "avg_speed": round(sum(speeds) / len(speeds), 1),
        "brake_ratio": round(brakes * 100.0 / len(frames), 1),
        "frames": frames,
    }


def telemetry_cached(fp):
    os.makedirs(TEL_DIR, exist_ok=True)
    try:
        st = os.stat(fp)
    except OSError:
        return None
    key = hashlib.md5(("%s|%d|%d" % (fp, st.st_mtime_ns, st.st_size)).encode()).hexdigest()
    cache = os.path.join(TEL_DIR, key + ".json")
    if os.path.isfile(cache):
        try:
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    try:
        result = parse_telemetry(fp)
    except Exception:
        result = None
    if result:
        try:
            tmp = cache + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(result, f, separators=(",", ":"))
            os.replace(tmp, cache)
        except OSError:
            pass
    return result


# ---------- 访问认证 ----------
# 默认关闭; 只要配了密码 (auth.conf 里的 pass= 或 sha256=) 就全站开启。
# 除登录页外, 页面/媒体流/下载/接口全部要过认证; 用 HMAC 签名 cookie (默认 30 天) 记住登录,
# 这样 <video>、缩略图、zip 下载这些子请求不会反复弹窗; 同时兼容 HTTP Basic 方便 curl。
# 配置文件: /opt/tesla-sentry/auth.conf  (user= / pass= / sha256= / secret= / ttl=)
AUTH_FILE = os.environ.get("TSF_AUTH_FILE", "/opt/tesla-sentry/auth.conf")


AUTH_CONF_ERR = None   # 非空 = auth.conf 异常, 启动时拒绝运行, 避免静默变成无认证


def _auth_conf():
    """读 auth.conf。文件不存在 = 用户主动关闭认证 (保留这个用法);
    文件存在却读不了 / 读不出凭据 = 配置损坏, 记下来由启动流程拒绝运行。"""
    global AUTH_CONF_ERR
    d = {}
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                d[k.strip().lower()] = v.strip()
    except FileNotFoundError:
        pass
    except OSError as ex:
        AUTH_CONF_ERR = "auth.conf 读取失败: %s" % ex
    return d


_AC = _auth_conf()
AUTH_USER = os.environ.get("TSF_AUTH_USER") or _AC.get("user") or "admin"
AUTH_PASS = os.environ.get("TSF_AUTH_PASS") or _AC.get("pass") or ""
AUTH_SHA = (os.environ.get("TSF_AUTH_SHA256") or _AC.get("sha256") or "").strip().lower()
AUTH_SECRET = (os.environ.get("TSF_AUTH_SECRET") or _AC.get("secret") or "") or ("tsf-v1|" + (AUTH_SHA or AUTH_PASS))
AUTH_TTL = int(os.environ.get("TSF_AUTH_TTL") or _AC.get("ttl") or 30 * 86400)
AUTH_ON = bool(AUTH_SHA or AUTH_PASS)
if (AUTH_CONF_ERR is None and os.path.exists(AUTH_FILE) and not AUTH_ON
        and not (os.environ.get("TSF_AUTH_PASS") or os.environ.get("TSF_AUTH_SHA256"))):
    AUTH_CONF_ERR = "auth.conf 存在但没有 pass= / sha256=, 内容可能已损坏"
FAILS = {}                       # ip -> [失败次数, 首次失败时间]
FAIL_LOCK = threading.Lock()


def pw_ok(pw):
    if AUTH_SHA:
        return hmac.compare_digest(hashlib.sha256(pw.encode()).hexdigest(), AUTH_SHA)
    return hmac.compare_digest((pw or "").encode(), AUTH_PASS.encode())


def auth_set_pass(old, new):
    """改网页登录密码; 返回 (成功?, 提示)。成功后重写 auth.conf 与内存凭据,
    并换一个随机 secret → 其它设备上的旧 cookie 立刻失效, 只有本机(前端会用新密码重新登录)继续。"""
    global AUTH_PASS, AUTH_SHA, AUTH_SECRET, AUTH_ON
    if os.environ.get("TSF_AUTH_PASS") or os.environ.get("TSF_AUTH_SHA256"):
        return False, "密码由环境变量 TSF_AUTH_PASS 控制, 改文件不生效"
    if AUTH_ON and not pw_ok(old or ""):
        return False, "原密码不对"
    n = (new or "").strip()
    if len(n) < 6:
        return False, "新密码至少 6 位"
    if AUTH_ON and hmac.compare_digest(n.encode(), (AUTH_PASS or "").encode()):
        return False, "新密码不能和原密码一样"
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    newsec = hashlib.sha256(os.urandom(32)).hexdigest()
    out, has_pass, has_sec = [], False, False
    for ln in lines:
        s = ln.strip()
        k = s.split("=", 1)[0].strip().lower() if ("=" in s and not s.startswith("#")) else ""
        if k == "pass":
            out.append("pass=" + n)
            has_pass = True
        elif k == "sha256":
            continue                       # 老写法统一换成 pass=
        elif k == "secret":
            out.append("secret=" + newsec)
            has_sec = True
        else:
            out.append(ln)
    if not has_pass:
        out.append("pass=" + n)
    if not has_sec:
        out.append("secret=" + newsec)
    try:
        os.makedirs(os.path.dirname(AUTH_FILE) or ".", exist_ok=True)
        tmp = AUTH_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, AUTH_FILE)
    except OSError as ex:
        return False, "写入失败: %s" % str(ex)[:80]
    AUTH_PASS, AUTH_SHA, AUTH_SECRET, AUTH_ON = n, "", newsec, True
    return True, "密码已更新, 其它设备需要重新登录"


def sess_token(exp):
    raw = "%s|%d" % (AUTH_USER, exp)
    sig = hmac.new(AUTH_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return "%d.%s" % (exp, sig)


def sess_ok(tok):
    try:
        exp = int(tok.split(".", 1)[0])
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(tok, sess_token(exp))


def fail_check(ip, inc):
    """同一 IP 10 分钟内连错 8 次就锁 5 分钟, 防爆破"""
    now = time.time()
    with FAIL_LOCK:
        cnt, t0 = FAILS.get(ip, [0, now])
        if now - t0 > 600:
            cnt, t0 = 0, now
        if inc:
            cnt += 1
        FAILS[ip] = [cnt, t0]
        return cnt >= 8


LOGIN_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__L_LOGIN__ · __L_BRAND__</title>
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:#0d1219;color:#e8eef4;font:15px/1.5 -apple-system,"SF Pro Text","PingFang SC","Microsoft YaHei",sans-serif;
 padding:24px}
.card{width:100%;max-width:340px;background:#161d26;border:1px solid #232c36;border-radius:18px;
 padding:26px 22px 22px;box-shadow:0 18px 50px rgba(0,0,0,.45)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:18px}
.dot{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#10b981,#059669);
 display:flex;align-items:center;justify-content:center;font-size:17px}
h1{font-size:16px;margin:0;font-weight:600;letter-spacing:.2px}
p.sub{margin:2px 0 0;font-size:12px;color:#7d8894}
label{display:block;font-size:12px;color:#97a3af;margin:12px 0 6px}
input{width:100%;padding:11px 12px;border-radius:11px;border:1px solid #2b3540;background:#0f151d;
 color:#e8eef4;font-size:15px;outline:none}
input:focus{border-color:#10b981;box-shadow:0 0 0 3px rgba(16,185,129,.15)}
button{width:100%;margin-top:18px;padding:12px;border:0;border-radius:11px;cursor:pointer;
 background:#10b981;color:#fff;font-size:15px;font-weight:600}
button:active{background:#0ea472}
.err{margin-top:12px;font-size:13px;color:#f87171;min-height:18px}
.foot{margin-top:16px;font-size:11px;color:#5f6b78;text-align:center}
</style></head><body>
<form class="card" method="post" action="/login">
  <div class="logo"><div class="dot">🚗</div>
    <div><h1>__L_BRAND__</h1><p class="sub">Tesla Sentry Drive</p></div></div>
  <label>__L_USER__</label>
  <input name="u" autocomplete="username" autocapitalize="none" autocorrect="off" value="__USER__">
  <label>__L_PASS__</label>
  <input name="p" type="password" autocomplete="current-password" autofocus>
  <input type="hidden" name="next" value="__NEXT__">
  <button type="submit">__L_LOGIN__</button>
  <div class="err">__MSG__</div>
  <div class="foot">__L_FOOT__</div>
</form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    timeout = SOCK_TIMEOUT   # 慢连接/半包请求超时断开, 避免长期占用线程

    def log_message(self, fmt, *args):
        pass

    # ---- 认证 ----
    def _cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _cookie_secure(self):
        """只有走 HTTPS (反代传 X-Forwarded-Proto 或显式开启) 才加 Secure;
        纯 HTTP 直连加了会导致浏览器不回传 cookie、谁都登录不上。"""
        if os.environ.get("TSF_COOKIE_SECURE") == "1":
            return "; Secure"
        if (self.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https":
            return "; Secure"
        return ""

    def _authed(self):
        if not AUTH_ON:
            return True
        if sess_ok(self._cookie("tsf_sess")):
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            ip = self.client_address[0] if self.client_address else "?"
            if fail_check(ip, False):        # 与登录页共用限速: 锁定期不再校验密码
                return False
            try:
                u, _, p = base64.b64decode(h[6:]).decode("utf-8", "replace").partition(":")
            except Exception:
                fail_check(ip, True)
                return False
            if hmac.compare_digest(u.encode(), AUTH_USER.encode()) and pw_ok(p):
                with FAIL_LOCK:
                    FAILS.pop(ip, None)
                return True
            fail_check(ip, True)
            return False
        return False

    def _login_page(self, msg="", nxt="/", code=200):
        lang = lang_from_accept(self.headers.get("Accept-Language") or "", "zh")
        days = str(max(1, AUTH_TTL // 86400))
        foot = tr("仅限本人访问", lang) + " · " + tr("登录状态保留", lang) + " " + days + " " + tr("天", lang)
        html = (LOGIN_HTML.replace("__USER__", AUTH_USER)
                .replace("__NEXT__", nxt.replace('"', "&quot;"))
                .replace("__MSG__", tr(msg, lang))
                .replace("__L_BRAND__", tr("特斯拉哨兵云盘", lang))
                .replace("__L_USER__", tr("用户名", lang))
                .replace("__L_PASS__", tr("密码", lang))
                .replace("__L_LOGIN__", tr("登录", lang))
                .replace("__L_FOOT__", foot))
        if lang != "zh":
            html = html.replace('<html lang="zh">', '<html lang="%s">' % lang)
        self._send(code, html.encode())

    def _do_login(self):
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        try:
            f = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "ignore"))
        except Exception:
            f = {}
        nxt = (f.get("next", ["/"])[0] or "/")
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        ip = self.client_address[0] if self.client_address else "?"
        if fail_check(ip, False):
            self._login_page("尝试次数过多，请 5 分钟后再试", nxt, 429)
            return
        if f.get("u", [""])[0] == AUTH_USER and pw_ok(f.get("p", [""])[0]):
            fail_check(ip, False)
            with FAIL_LOCK:
                FAILS.pop(ip, None)
            exp = int(time.time()) + AUTH_TTL
            self.send_response(302)
            self.send_header("Location", nxt)
            self.send_header("Set-Cookie",
                             "tsf_sess=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s"
                             % (sess_token(exp), AUTH_TTL, self._cookie_secure()))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        fail_check(ip, True)
        time.sleep(0.4)          # 轻微延迟, 拖慢暴力破解
        self._login_page("用户名或密码不对", nxt, 401)

    def _gate(self):
        """未登录: 页面跳登录页, 媒体/接口直接 401。返回 True 表示可继续"""
        if not AUTH_ON:
            return True
        p = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if p in ("/login", "/logout"):
            return True
        if self._authed():
            return True
        if p in ("/", "/index.html") or "text/html" in (self.headers.get("Accept") or ""):
            self.send_response(302)
            self.send_header("Location", "/login?next=" + urllib.parse.quote(self.path or "/"))
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(401, "unauthorized".encode(), "text/plain; charset=utf-8")
        return False

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 页面/接口一律禁止缓存: 之前 HTML 没有任何缓存头, iOS Safari 会把旧页面留在内存里,
        # 升级后手机上仍然显示旧界面 (典型症状: 已经改了样式, 手机上还是老样子)
        if "text/html" in ctype or "application/json" in ctype:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def _read_json(self, limit=MAX_JSON):
        """读 JSON 请求体: 超过上限返回 "TOOBIG", 解析失败返回 None, 空体返回 {}。
        不用裸 rfile.read(Content-Length), 免得一个大包把 1.4G 内存吃满。"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        if n > limit:
            return "TOOBIG"
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "ignore") or "{}")
        except Exception:
            return None

    def _read_upload(self):
        """解析 multipart: 返回 (文件名, 文件数据, 普通字段dict)"""
        ctype = self.headers.get("Content-Type", "")
        mm = re.search(r'boundary=([^;]+)', ctype)
        if "multipart/form-data" not in ctype or not mm:
            return None, None, {}
        boundary = mm.group(1).strip('"').encode()
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_UPLOAD:
            return "TOOBIG", None, {}
        body = self.rfile.read(length)
        fields = {}
        file_fn = None
        file_data = None
        for part in body.split(b"--" + boundary):
            i = part.find(b"\r\n\r\n")
            if i == -1:
                continue
            headers = part[:i].decode("utf-8", "replace")
            data = part[i + 4:]
            if data.endswith(b"\r\n"):
                data = data[:-2]
            nm = re.search(r'name="([^"]*)"', headers)
            if 'filename="' in headers:
                fn = re.search(r'filename="([^"]*)"', headers)
                if fn and fn.group(1) and data:
                    file_fn, file_data = fn.group(1), data
            elif nm and nm.group(1):
                fields[nm.group(1)] = data.decode("utf-8", "replace").strip()
        return file_fn, file_data, fields

    def do_HEAD(self):
        """只回头部: iOS/安卓下载器在真正下载前常先 HEAD 探大小, 不支持会退化成"边播边存"的残缺文件"""
        def hdr_only(code, extra=None):
            self.send_response(code)
            for k, v in (extra or []):
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()

        if not self._gate():
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path == "/dl":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fp = safe_archive_path(q.get("path", [""])[0])
            if not fp:
                hdr_only(404)
                return
            out = fp
            qv = q.get("q", [""])[0]
            if qv in RES_SCALE:
                out = get_variant(fp, qv)
            serve_file(self, out, head_only=True)
            return
        m = re.match(r"^/media/([\w-]+)/([\w\-. ]+)/([\w\-. ]+)$", path)
        if m:
            fp = os.path.normpath(os.path.join(ARCHIVE, m.group(1), m.group(2), m.group(3)))
            if not fp.startswith(ARCHIVE) or not os.path.isfile(fp):
                hdr_only(404)
                return
            try:
                size = str(os.path.getsize(fp))
            except OSError:
                hdr_only(404)
                return
            hdr_only(200, [("Content-Type", "video/mp4"), ("Content-Length", size),
                           ("Accept-Ranges", "bytes")])
            return
        if path.startswith("/api/export/"):
            hdr_only(200, [("Content-Type", "video/mp4"), ("Accept-Ranges", "bytes")])
            return
        hdr_only(405)

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path == "/login":
            if not AUTH_ON:
                self.send_response(302)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self._login_page("", q.get("next", ["/"])[0])
            return
        if path == "/logout":
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Set-Cookie", "tsf_sess=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax%s" % self._cookie_secure())
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/ver":
            # 版本探针: 前端刷新时对比, 服务端升级了就自动重载, 不用手动清缓存
            body = json.dumps({"v": APP_VERSION}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._gate():
            return
        if path in ("/", "/index.html"):
            # 二维码 URL 带内容版本号: 换码后旧图缓存立即失效, 避免浏览器显示未裁剪的旧二维码
            sponsor_ver = hashlib.md5(b"".join(SPONSOR_BYTES[k] for k in sorted(SPONSOR_BYTES))).hexdigest()[:10]
            html = (HTML.replace("__CAT_NAMES__", json.dumps(CAT_NAMES, ensure_ascii=False))
                    .replace("__I18N__", I18N_JS).replace("__LANGS__", LANG_JS)
                    .replace("__APP_VER__", APP_VERSION)
                    .replace("__SPONSOR_VER__", sponsor_ver))
            self._send(200, html.encode())
        elif path.startswith("/sponsor/"):
            # 赞助/联系作者二维码: 从单文件内嵌资源按需返回, 不占首屏 HTML 体积
            # src 带 ?v=<内容hash> 时可长期强缓存; 不带版本号的旧链接一律不缓存, 防止陈旧图残留
            key = path[len("/sponsor/"):].split("?")[0]
            data = SPONSOR_BYTES.get(key)
            if not data:
                self._send(404, b"not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=604800, immutable" if "?v=" in self.path else "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        elif path == "/api/stitch/status":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            job = STITCH_JOBS.get(q.get("id", [""])[0])
            if not job:
                self._json({"state": "error", "err": "任务不存在或已过期"}, 404)
                return
            self._json({"state": job["state"], "pct": job.get("pct", 0),
                        "err": job.get("err", ""), "size": job.get("size", 0)})
        elif path == "/api/stitch/dl":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            jid = q.get("id", [""])[0]
            job = STITCH_JOBS.get(jid)
            if not job or job.get("state") != "done" or not os.path.isfile(job.get("path") or ""):
                self._send(404, b"not ready")
                return
            fp = job["path"]
            fname = job.get("name") or "stitch.mp4"
            size = os.path.getsize(fp)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % urllib.parse.quote(fname))
            self.end_headers()
            with open(fp, "rb") as f:
                while True:
                    chunk = f.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            with STITCH_LOCK:
                STITCH_JOBS.pop(jid, None)
            try:
                os.unlink(fp)
            except OSError:
                pass
        elif path == "/api/export/burn/dl":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            jid = q.get("id", [""])[0]
            job = STITCH_JOBS.get(jid)
            if not job or job.get("state") != "done" or not os.path.isfile(job.get("path") or ""):
                self._send(404, b"not ready")
                return
            fp = job["path"]
            fname = job.get("name") or "teslacam.mp4"
            try:
                size = os.path.getsize(fp)
            except OSError:
                self._send(404, b"gone")
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            # ASCII 兜底文件名 + UTF-8 原名: iOS 下载器对百分号编码的中文名会显示成乱码
            self.send_header("Content-Disposition",
                             'attachment; filename="teslacam.mp4"; filename*=UTF-8\'\'%s'
                             % urllib.parse.quote(fname))
            self.end_headers()
            try:
                with open(fp, "rb") as f:
                    while True:
                        chunk = f.read(262144)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            with STITCH_LOCK:
                STITCH_JOBS.pop(jid, None)
            try:
                os.unlink(fp)
            except OSError:
                pass
        elif path == "/api/events":
            self._json(list_events())
        elif path == "/api/live/status":
            folder, newest = live_latest()
            self._json({"ok": True, "folder": folder,
                        "age": round(newest - time.time(), 1) if newest else -1,
                        "car_age": round(usb_img_age(), 1)})
        elif path == "/api/telemetry":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            p = q.get("path", [""])[0]
            fp = safe_archive_dir(p) if p else None
            if not fp or not os.path.isfile(fp):
                self._json({"ok": False, "reason": "bad-path"}, 404)
                return
            tel = telemetry_cached(fp)
            if not tel:
                self._json({"ok": False, "reason": "no-sei"})
                return
            self._json({"ok": True, **tel})
        elif path == "/api/live":
            self._json(live_status())
        elif path == "/api/auth/info":
            self._json({"user": AUTH_USER, "on": AUTH_ON})
        elif path == "/api/notify":
            self._json(notify_status())
        elif path == "/api/ota/check":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            self._json(ota_check(force=q.get("force", ["0"])[0] == "1"))
        elif path == "/api/settings":
            self._json({"ok": True, "auto_update": load_settings().get("auto_update", True),
                        "auto_checked": AUTO_UPD["last_check"], "auto_msg": AUTO_UPD["last_result"]})
        elif path == "/api/skins":
            skins = sorted(f for f in os.listdir(SKIN_DIR)
                           if f != "active.json" and os.path.isfile(os.path.join(SKIN_DIR, f)))
            self._json({"skins": skins, "active": active_skin()})
        elif path == "/api/skin":
            s = active_skin()
            if s and s.endswith(".css") and os.path.isfile(os.path.join(SKIN_DIR, s)):
                with open(os.path.join(SKIN_DIR, s), "rb") as f:
                    self._send(200, f.read(), "text/css; charset=utf-8")
            else:
                self._send(204, b"", "text/css")
        elif path.startswith("/skin-media/"):
            fn = safe_name(path[len("/skin-media/"):])
            fp = os.path.join(SKIN_DIR, fn) if fn else ""
            if fn and os.path.isfile(fp):
                ext = fn.rsplit(".", 1)[-1].lower()
                ct = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                      "webp": "image/webp", "css": "text/css"}.get(ext, "application/octet-stream")
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ct)
            else:
                self._send(404, b"not found")
        elif path == "/api/audio":
            q = sorted(f for f in os.listdir(AUDIO_Q) if os.path.isfile(os.path.join(AUDIO_Q, f)))
            d = sorted(f for f in os.listdir(AUDIO_DONE) if os.path.isfile(os.path.join(AUDIO_DONE, f)))
            self._json({"queue": q, "done": d})
        elif path == "/api/browse":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            d = safe_archive_dir(q.get("dir", [""])[0])
            if not d or not os.path.isdir(d):
                self._json({"ok": False, "msg": "目录不存在或不在 /archive 内"}, 404)
                return
            dirs, files = [], []
            for e in sorted(os.listdir(d)):
                ep = os.path.join(d, e)
                if os.path.isdir(ep):
                    dirs.append(e)
                elif os.path.isfile(ep):
                    files.append({"name": e, "size": os.path.getsize(ep)})
            self._json({"ok": True, "dir": d, "dirs": dirs, "files": files})
        elif path == "/api/usb/browse":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fp = usb_real(q.get("dir", [""])[0])
            if not usb_attached or not os.path.ismount(MNT):
                self._json({"ok": False, "attached": False, "msg": "车机U盘未连接"})
                return
            if fp is None or not os.path.isdir(fp):
                self._json({"ok": False, "attached": True, "msg": "目录不存在"}, 404)
                return
            dirs, files = [], []
            for e in sorted(os.listdir(fp)):
                ep = os.path.join(fp, e)
                if os.path.isdir(ep):
                    dirs.append(e)
                elif os.path.isfile(ep):
                    files.append({"name": e, "size": os.path.getsize(ep)})
            rel = os.path.relpath(fp, MNT).replace("\\", "/")
            self._json({"ok": True, "attached": True, "dir": "" if rel == "." else rel,
                        "dirs": dirs, "files": files})
        elif path == "/usbdl":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fp = usb_real(q.get("path", [""])[0])
            if not fp or not os.path.isfile(fp):
                self._send(404, b"not found")
                return
            serve_file(self, fp)
        elif path == "/dl":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fp = safe_archive_path(q.get("path", [""])[0])
            if not fp:
                self._send(404, b"not found")
                return
            out = fp
            qv = q.get("q", [""])[0]
            if qv in RES_SCALE:  # 下载也支持清晰度: 转码缓存副本, 失败回退原文件
                out = get_variant(fp, qv)
            serve_file(self, out)
        elif path.startswith("/api/export/"):
            m2 = re.match(r"^/api/export/([\w-]+)/([\w\-. ]+)/?$", path)
            if not m2:
                self._send(404, b"not found")
                return
            q2 = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            cams = [c.strip() for c in (q2.get("cams", [""])[0] or "").split(",") if c.strip()]
            evdir = safe_archive_dir(ARCHIVE + "/" + m2.group(1) + "/" + m2.group(2))
            if not evdir or not os.path.isdir(evdir):
                self._send(404, b"not found")
                return
            vids = []
            allf = latest_group(sorted(f for f in os.listdir(evdir) if f.endswith(".mp4")))
            for f in allf:
                cam = f.rsplit("-", 1)[-1][:-4] if "-" in f else f
                if not cams or cam in cams:
                    vids.append(f)
            if not vids:
                self._send(404, b"no matching videos")
                return
            evname = m2.group(2)
            zname = evname + (("_" + "-".join(cams)) if cams and len(cams) < 6 else "") + ".zip"
            qv = q2.get("q", [""])[0]  # 打包zip也支持清晰度: 每路先取转码副本 (串行转码), 失败回退原文件
            if qv not in RES_SCALE:
                qv = ""
            tmp = tempfile.NamedTemporaryFile(prefix="exp_", suffix=".zip", delete=False)
            tmp.close()
            try:
                with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as z:
                    for f in vids:
                        ffp = os.path.join(evdir, f)
                        if qv:
                            ffp = get_variant(ffp, qv)
                        z.write(ffp, arcname=evname + "/" + f)
                    ej = os.path.join(evdir, "event.json")
                    if os.path.isfile(ej):
                        z.write(ej, arcname=evname + "/event.json")
                zsize = os.path.getsize(tmp.name)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(zsize))
                self.send_header("Content-Disposition",
                                 'attachment; filename="%s"' % urllib.parse.quote(zname))
                self.end_headers()
                with open(tmp.name, "rb") as zf:
                    while True:
                        chunk = zf.read(262144)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
        elif path.startswith("/thumb/"):
            self._serve_thumb(path)
        else:
            m = re.match(r"^/media/([\w-]+)/([\w\-. ]+)/([\w\-. ]+)$", path)
            if not m:
                self._send(404, b"not found")
                return
            fp = os.path.normpath(os.path.join(ARCHIVE, m.group(1), m.group(2), m.group(3)))
            if not fp.startswith(ARCHIVE) or not os.path.isfile(fp):
                self._send(404, b"not found")
                return
            # 清晰度: ?q=360p/480p/720p 时返回按需转码的缓存副本 (失败回退原文件)
            qv = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", ["orig"])[0]
            if qv in RES_SCALE and qv != "orig":
                fp = get_variant(fp, qv)
            size = os.path.getsize(fp)
            rng = self.headers.get("Range")
            if rng:
                # 支持三种形式: bytes=a-b / bytes=a- / bytes=-n (尾部 n 字节)
                mm2 = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
                if not mm2:
                    self._send(416, b"bad range")
                    return
                s1, s2 = mm2.group(1), mm2.group(2)
                if s1 == "":
                    # 尾部范围: 浏览器读 mp4 moov 时常用, 不支持会让视频一直卡在 readyState 0
                    n = int(s2 or 0)
                    start = max(0, size - n)
                    end = size - 1
                else:
                    start = int(s1)
                    end = int(s2) if s2 else size - 1
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                if end < start:
                    end = size - 1
            else:
                start, end = 0, size - 1
            length = end - start + 1
            # 并发限流: 浏览器一次会为几十个缩略图发请求, 不限流会把设备拖死
            if not MEDIA_SEM.acquire(timeout=20):
                self._send(503, b"busy")
                return
            MEDIA_ACTIVE[0] += 1
            try:
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if rng:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with open(fp, "rb") as f:
                    f.seek(start)
                    remain = length
                    while remain > 0:
                        chunk = f.read(min(65536, remain))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remain -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                MEDIA_ACTIVE[0] = max(0, MEDIA_ACTIVE[0] - 1)
                MEDIA_SEM.release()

    def _serve_thumb(self, path):
        """列表缩略图: 用 ffmpeg 抽指定秒数一帧 (?t=秒, 默认 1), 结果按 (路径+大小+mtime+t) 哈希缓存在 eMMC"""
        parts = path[len("/thumb/"):].split("/")
        if len(parts) != 3:
            self._send(404, b"not found")
            return
        cat, name, fn = [urllib.parse.unquote(p) for p in parts]
        evdir = safe_archive_dir(os.path.join(ARCHIVE, cat, name))
        fn = safe_name(fn)
        if not evdir or not fn:
            self._send(404, b"not found")
            return
        fp = os.path.join(evdir, fn)
        if not os.path.isfile(fp):
            self._send(404, b"not found")
            return
        try:
            st = os.stat(fp)
        except OSError:
            self._send(404, b"not found")
            return
        try:
            ss_t = max(0.0, min(float(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("t", ["1"])[0]), 3600.0))
        except ValueError:
            ss_t = 1.0
        out = thumb_file(fp, ss_t)
        if not out:
            self._send(404, b"no thumb")
            return
        try:
            with open(out, "rb") as f:
                data = f.read()
        except OSError:
            self._send(404, b"not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=604800")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        p = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if p == "/login" and AUTH_ON:
            self._do_login()
            return
        if not self._gate():
            return
        path = urllib.parse.unquote(self.path)
        if p == "/api/fm":
            body = self._read_json()
            if body == "TOOBIG":
                self._json({"ok": False, "msg": "请求体过大 (上限 1MB)"}, 413)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "msg": "参数错误"}, 400)
                return
            try:
                ok, msg, done, failed, extra = fm_action(
                    str(body.get("area") or ""), str(body.get("op") or ""),
                    body.get("items") or [], body.get("dest") or "", body.get("name") or "")
            except Exception as ex:
                self._json({"ok": False, "msg": "操作失败: %s" % ex}, 500)
                return
            j = {"ok": ok, "msg": msg, "done": done, "failed": failed}
            j.update(extra)
            self._json(j)
            return
        if path == "/api/auth/pass":
            body = self._read_json()
            if body == "TOOBIG":
                self._json({"ok": False, "msg": "请求体过大 (上限 1MB)"}, 413)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "msg": "参数错误"}, 400)
                return
            ok, msg = auth_set_pass(str(body.get("old") or ""), str(body.get("new") or ""))
            self._json({"ok": ok, "msg": msg, "user": AUTH_USER})
            return
        if path == "/api/notify/save":
            body = self._read_json()
            if body == "TOOBIG":
                self._json({"ok": False, "msg": "请求体过大 (上限 1MB)"}, 413)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "msg": "参数错误"}, 400)
                return
            patch = {}
            for k in ("enabled", "saved"):
                if k in body:
                    patch[k] = bool(body.get(k))
            for k in ("mode", "media", "quality", "webhook", "app_id", "receive_id",
                      "receive_id_type", "public_url"):
                if k in body:
                    patch[k] = str(body.get(k) or "").strip()
            if str(body.get("app_secret") or "").strip():
                patch["app_secret"] = str(body["app_secret"]).strip()
            if "delay" in body:
                try:
                    patch["delay"] = max(5, min(600, int(body["delay"])))
                except (TypeError, ValueError):
                    pass
            if "vdur" in body:
                try:
                    patch["vdur"] = max(10, min(60, int(body["vdur"])))
                except (TypeError, ValueError):
                    pass
            if str(patch.get("quality") or "") not in ("360p", "480p", "720p"):
                patch.pop("quality", None)
            if str(patch.get("media") or "") not in ("thumb", "video"):
                patch.pop("media", None)
            conf = notify_save(patch)
            if _dm_ready(conf):
                warn = "" if (conf.get("public_url") or "").strip() else "私聊已可用; 填「查看链接前缀」后卡片上会有查看按钮"
            elif str(conf.get("webhook") or "").strip():
                warn = "私聊未就绪 (需 App ID/Secret + 接收人), 当前会推到群里"
            else:
                warn = "还没配接收人: 私聊需要 App ID/Secret + 接收人, 或填群机器人 webhook"
            self._json({"ok": True, "msg": "已保存", "status": notify_status(), "warn": warn})
            return
        if path == "/api/notify/test":
            cat, name = "SentryClips", ""
            try:
                base = os.path.join(ARCHIVE, "SentryClips")
                cands = sorted(e for e in os.listdir(base)
                               if re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", e))
                name = cands[-1] if cands else ""
            except OSError:
                pass
            if not name:
                self._json({"ok": False, "msg": "归档里还没有哨兵事件, 无法生成测试卡片"}, 400)
                return
            ok, msg = notify_push(cat, name, test=True)
            self._json({"ok": ok, "msg": msg})
            return
        if urllib.parse.urlsplit(self.path).path == "/api/push/upload":
            # 手动推送: 浏览器本机合成的片段 (webm/mp4 裸流) 上传到设备, 转码后推飞书私聊
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            cat = q.get("cat", [""])[0]
            name = q.get("name", [""])[0]
            quality = q.get("q", ["480p"])[0]
            if quality not in ("360p", "480p", "720p", "orig"):
                quality = "480p"
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not name or not re.match(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", name):
                self._json({"ok": False, "msg": "参数错误"}, 400)
                return
            if length <= 0 or length > 300 * 1048576:
                self._json({"ok": False, "msg": "片段为空或超过 300MB"}, 400)
                return
            evdir = safe_archive_dir(os.path.join(ARCHIVE, cat, name))
            if not evdir or not os.path.isdir(evdir):
                self._json({"ok": False, "msg": "事件不存在"}, 400)
                return
            tmp = "/tmp/tsf-push-upload.%d" % os.getpid()
            try:
                with open(tmp, "wb") as f:
                    rem = length
                    while rem > 0:
                        chunk = self.rfile.read(min(1 << 20, rem))
                        if not chunk:
                            break
                        f.write(chunk)
                        rem -= len(chunk)
                ok, msg = manual_push(notify_conf(), tmp, cat, name, quality)
            except Exception as ex:
                ok, msg = False, "推送失败: %s" % str(ex)[:160]
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            self._json({"ok": ok, "msg": msg})
            return
        if path == "/api/ota/apply":
            ok, msg = ota_apply()
            self._json({"ok": ok, "msg": msg})
            return
        if path == "/api/ota/rollback":
            ok, msg = ota_rollback()
            self._json({"ok": ok, "msg": msg})
            return
        if path == "/api/ota/auto":
            body = self._read_json()
            if body == "TOOBIG" or not isinstance(body, dict):
                self._json({"ok": False, "msg": "参数错误"}, 400)
                return
            on = bool(body.get("on"))
            save_settings({"auto_update": on})
            if on:
                AUTO_UPD["next_try"] = 0        # 刚打开就尽快检查一次, 不用等下一轮
            self._json({"ok": True, "auto": on})
            return
        if path == "/api/export/burn":
            body = self._read_json()
            if body == "TOOBIG":
                self._json({"ok": False, "error": "请求体过大 (上限 1MB)"}, 413)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "error": "参数错误"}, 400)
                return
            jid, err = start_export(body.get("cat") or "", body.get("name") or "",
                                    body.get("cams") or [], body.get("wm", True),
                                    body.get("q") or "720p", body.get("files"))
            if not jid:
                self._json({"ok": False, "error": err}, 400)
                return
            self._json({"ok": True, "id": jid})
            return
        if path == "/api/stitch":
            body = self._read_json()
            if body == "TOOBIG":
                self._json({"ok": False, "error": "请求体过大 (上限 1MB)"}, 413)
                return
            if not isinstance(body, dict):
                self._json({"ok": False, "error": "参数错误"}, 400)
                return
            jid, err = start_stitch(body.get("cat") or "", body.get("name") or "", body.get("cams") or [])
            if not jid:
                self._json({"ok": False, "error": err}, 400)
                return
            self._json({"ok": True, "id": jid})
            return
        if path == "/api/upload/skin":
            fn, data, _f = self._read_upload()
            if fn == "TOOBIG":
                self._json({"ok": False, "msg": "文件超过 3MB"}, 400)
                return
            fn = safe_name(fn)
            if not fn or not data or not fn.lower().endswith((".css", ".jpg", ".jpeg", ".png", ".webp")):
                self._json({"ok": False, "msg": "仅支持 css/jpg/png/webp"}, 400)
                return
            with open(os.path.join(SKIN_DIR, fn), "wb") as f:
                f.write(data)
            activated = False
            if fn.endswith(".css"):
                with open(os.path.join(SKIN_DIR, "active.json"), "w") as f:
                    json.dump({"skin": fn}, f)
                activated = True
            self._json({"ok": True, "msg": ("皮肤已上传并应用" if activated else "图片已上传, 请在 CSS 中用 /skin-media/" + fn), "activated": activated})
        elif path == "/api/upload/audio":
            fn, data, _f = self._read_upload()
            if fn == "TOOBIG":
                self._json({"ok": False, "msg": "文件超过 3MB (车机要求 <1MB)"}, 400)
                return
            fn = safe_name(fn)
            if not fn or not data or not fn.lower().endswith((".wav", ".mp3")):
                self._json({"ok": False, "msg": "仅支持 wav/mp3"}, 400)
                return
            if len(data) > 1024 * 1024:
                self._json({"ok": False, "msg": "车机要求单个音频 <1MB, 请裁剪后重试"}, 400)
                return
            with open(os.path.join(AUDIO_Q, fn), "wb") as f:
                f.write(data)
            self._json({"ok": True, "msg": "已加入队列, 归档同步时写入车机U盘"})
        elif path == "/api/usb/attach":
            ok, msg = usb_attach()
            self._json({"ok": ok, "msg": "车机U盘已连接 (已临时断开车机读取, 5分钟无操作自动恢复)" if ok and msg != "already" else ("已处于管理状态" if ok else msg)})
        elif path == "/api/usb/detach":
            ok, msg = usb_detach()
            self._json({"ok": ok, "msg": "已恢复车机导出" if ok else msg})
        elif path == "/api/upload/usb":
            fn, data, fields = self._read_upload()
            if fn == "TOOBIG":
                self._json({"ok": False, "msg": "文件超过 100MB 上限"}, 400)
                return
            fn = safe_name(fn)
            if not fn or not data:
                self._json({"ok": False, "msg": "无效的文件"}, 400)
                return
            if not usb_attached or not os.path.ismount(MNT):
                self._json({"ok": False, "msg": "车机U盘未连接, 请先点「连接车机U盘」"}, 400)
                return
            dest = usb_real(fields.get("path"))
            if dest is None or not os.path.isdir(dest):
                self._json({"ok": False, "msg": "目标目录不存在"}, 400)
                return
            with open(os.path.join(dest, fn), "wb") as f:
                f.write(data)
            subprocess.run(["sync"], capture_output=True)
            rel = os.path.relpath(dest, MNT).replace("\\", "/")
            self._json({"ok": True, "msg": f"已上传到 U盘/{'' if rel == '.' else rel + '/' }{fn} ({len(data)//1024} KB)"})
        elif path == "/api/upload/file":
            fn, data, fields = self._read_upload()
            if fn == "TOOBIG":
                self._json({"ok": False, "msg": "文件超过 100MB 上限"}, 400)
                return
            fn = safe_name(fn)
            if not fn or not data:
                self._json({"ok": False, "msg": "无效的文件"}, 400)
                return
            req_path = (fields.get("path") or "").strip()
            dest_dir = safe_archive_dir(req_path)
            if req_path and not dest_dir:
                self._json({"ok": False, "msg": "目标路径无效 (必须在 /archive 内)"}, 400)
                return
            if not dest_dir:
                dest_dir = FILES_DIR
            os.makedirs(dest_dir, exist_ok=True)
            with open(os.path.join(dest_dir, fn), "wb") as f:
                f.write(data)
            self._json({"ok": True, "msg": f"已上传到 {dest_dir}/{fn} ({len(data)//1024} KB)"})
        elif path == "/api/skin/activate":
            body = self._read_json()
            if body == "TOOBIG":
                self._json({"ok": False, "msg": "请求体过大 (上限 1MB)"}, 413)
                return
            name = safe_name(body.get("name") if isinstance(body, dict) else None)
            if name and os.path.isfile(os.path.join(SKIN_DIR, name)):
                with open(os.path.join(SKIN_DIR, "active.json"), "w") as f:
                    json.dump({"skin": name}, f)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "皮肤不存在"}, 404)
        elif path == "/api/audio/setlockchime":
            body = self._read_json()
            if body == "TOOBIG":
                self._json({"ok": False, "msg": "请求体过大 (上限 1MB)"}, 413)
                return
            name = safe_name(body.get("name") if isinstance(body, dict) else None)
            src = os.path.join(AUDIO_Q, name) if name else ""
            if name and os.path.isfile(src):
                with open(src, "rb") as f:
                    data = f.read()
                if not name.lower().endswith(".wav"):
                    self._json({"ok": False, "msg": "锁车音必须是 WAV (mp3 只能当喇叭音效)"})
                    return
                with open(os.path.join(AUDIO_Q, "LockChime.wav"), "wb") as f:
                    f.write(data)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "文件不存在"}, 404)
        else:
            self._send(404, b"not found")

    def do_DELETE(self):
        if not self._gate():
            return
        path = urllib.parse.unquote(self.path)
        if path.startswith("/api/skin/"):
            name = safe_name(path[len("/api/skin/"):])
            fp = os.path.join(SKIN_DIR, name) if name else ""
            if name and os.path.isfile(fp):
                os.remove(fp)
                if active_skin() == name:
                    os.remove(os.path.join(SKIN_DIR, "active.json"))
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "not found"}, 404)
        elif path.startswith("/api/audio/"):
            name = safe_name(path[len("/api/audio/"):].split("?")[0])
            done = "done=1" in self.path
            base = AUDIO_DONE if done else AUDIO_Q
            fp = os.path.join(base, name) if name else ""
            if name and os.path.isfile(fp) and name != "LockChime.wav":
                os.remove(fp)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "not found"}, 404)
        elif path.startswith("/api/usb"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fp = usb_real(q.get("path", [""])[0])
            if not fp or not os.path.isfile(fp) or fp == MNT:
                self._json({"ok": False, "msg": "not found"}, 404)
                return
            os.remove(fp)
            subprocess.run(["sync"], capture_output=True)
            self._json({"ok": True})
        elif path.startswith("/api/file"):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            fp = safe_archive_path(q.get("path", [""])[0])
            if fp:
                os.remove(fp)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "msg": "not found"}, 404)
        else:
            self._send(404, b"not found")


class BoundedServer(ThreadingHTTPServer):
    """限并发的线程式服务: 设备只有 1.4G 内存, 无限开线程会被慢连接拖垮。"""
    daemon_threads = True
    max_workers = 48

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._slots = threading.BoundedSemaphore(self.max_workers)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


if __name__ == "__main__":
    # OTA 自检: 只验证模块能完整加载 (常量/函数定义无误), 不启动服务、不碰硬件
    if "--ota-check" in sys.argv:
        print("ok %s" % APP_VERSION)
        sys.exit(0)
    if AUTH_CONF_ERR:
        print("FATAL: %s" % AUTH_CONF_ERR)
        print("为避免无认证暴露, 服务不启动。修复 %s 后重启 (或重跑 install.sh 重新生成密码)。" % AUTH_FILE)
        sys.exit(2)
    ensure_dirs()
    # 降低自身被 OOM 杀掉的概率 (合成任务会自己调高优先级先死)
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("-300")
    except OSError:
        pass
    # 先绑定端口: 若已有实例在跑, 重复实例会在这里直接退出,
    # 不会执行下面的"启动自愈"而误拆别人正在管理的挂载点
    try:
        srv = BoundedServer(("0.0.0.0", PORT), Handler)
    except OSError:
        print("port %d in use, another instance running? exit." % PORT)
        sys.exit(0)
    print(f"Sentry viewer on 0.0.0.0:{PORT}")
    print("auth: %s (%s)" % ("ON, user=" + AUTH_USER if AUTH_ON else "OFF - 公网可直接访问!", AUTH_FILE))
    # 启动自愈: 若上次退出时镜像仍处于管理挂载状态, 恢复导出。
    # 短暂重试拿锁, 避免与正在进行的归档/同步互相拆台; 拿不到就完全不碰 U 盘。
    for _ in range(6):
        if _acquire_usb_flock():
            break
        time.sleep(0.4)
    if _usb_flock_fd is not None:
        # 自愈失败不能让整个服务起不来: losetup/umount 缺失或被删时只告警
        try:
            if os.path.ismount(MNT):
                subprocess.run(["umount", MNT], capture_output=True)
            subprocess.run([LOSETUP, "-d", LOOPDEV], capture_output=True)
        except OSError as ex:
            print("startup self-heal skipped: %s" % ex)
        _gadget_rebind(True)
        _release_usb_flock()
    else:
        print("startup self-heal skipped: usb lock held by another process")
    threading.Thread(target=disk_guard, daemon=True).start()
    threading.Thread(target=health_logger, daemon=True).start()
    threading.Thread(target=notify_loop, daemon=True).start()
    threading.Thread(target=auto_update_loop, daemon=True).start()
    srv.serve_forever()
