# LC boutique — asistent automat Telegram

Botul urmărește grupul privat în care postați produsele de la producători. Pentru fiecare produs:

1. identifică produsul (rochie, costum, bluză, geacă etc.), din poze și text;
2. preia exact codul, mărimile, materialul, compoziția, culorile, detaliile și măsurătorile;
3. traduce descrierea din ucraineană/rusă în română, scurt și frumos;
4. scoate datele producătorului (telefoane, conturi, adrese, manageri, depozit, prețuri);
5. calculează prețul: `(grivne × 0,50 sau dolari × 20) + 100 lei + profit`, rotunjit la un preț comercial (550, 590, 650, 680, 690…);
6. publică pe canalul LC boutique pozele originale + textul, cu secțiunea de comandă la final.

Nu cere confirmare. În grupul sursă primiți doar un reply scurt:
`✅ Publicat: Rochie midi — cod R-1452 — 650 lei` plus calculul, ca să îl puteți verifica (clientele nu îl văd).
Dacă lipsește codul sau prețul, produsul **nu** se publică și primiți un avertisment `⚠️`.

## Exemplu de postare

```
Rochie midi din tricot

Cod/Model: R-1452
Mărimi: 42-44, 46-48
Material: tricot (95% bumbac, 5% elastan)
Culori: negru, bej
💰 Preț: 650 lei

O rochie comodă și elegantă, cu croială dreaptă și lungime midi, potrivită pentru fiecare zi.

📩 Pentru comandă, trimiteți mesaj pe Instagram:
• poza produsului
• codul exact al produsului
• mărimea
• culoarea
• numărul de telefon
```

## Cum postați în grupul sursă

- **Poze + text = un produs.** Trimiteți/redirecționați oricâte albume de poze, apoi textul — pe ultimele poze
  sau ca mesaj separat. Tot ce a venit înainte de text devine un singur produs. Botul așteaptă textul până la
  3 minute de la ultima poză (`PHOTOS_WAIT_SECONDS`).
- Dacă textul vine primul, pozele trimise imediat după el (în max. 25 s) se atașează lui.
- Pe canal se publică întâi toate pozele (albume de maxim 10, limita Telegram, împărțite egal), iar descrierea
  apare la final, sub ultimul album.
  Pentru analiză asistentul vede primele 20 de poze, la rezoluție redusă (mai ieftin).
- Mai multe produse (coduri diferite) în aceeași postare: botul face câte o postare separată pentru fiecare și
  împarte pozele între ele. Cel mai sigur este totuși câte un album per produs.
- Produsele postate unul după altul se adună timp de 60 s de liniște, apoi se publică **grupate pe categorii**
  (rochii cu rochii, costume cu costume, …).

## Calculul prețului

| Categorie | Profit |
|---|---|
| malete, topuri, body-uri, bluze | 100–150 lei |
| rochii, cămăși, pantaloni, blugi, fuste, compleuri | 150–200 lei |
| costume, costume sport, sacouri, cardigane | 150–200 lei |
| veste, geci/scurte, paltoane | 200–300 lei |

Profitul exact din interval îl alege automat asistentul după preț, material și complexitatea modelului;
apoi prețul se rotunjește la cel mai apropiat preț cu terminația 50, 80 sau 90, fără a ieși din interval.

Se folosește întotdeauna **prețul drop**; dacă producătorul nu dă preț drop, se ia prețul opt, apoi prețul simplu,
apoi retail. Prețurile în dolari se calculează cu **1 $ = 20 lei** (`USD_TO_LEI`), apoi aceeași formulă:
`(dolari × 20) + 100 lei + profit`. Dacă lipsește un material, culoare etc., rândul respectiv pur și simplu nu apare.

## Instalare

1. **Botul**: la [@BotFather](https://t.me/BotFather) → `/newbot` → copiați tokenul.
   Apoi `/setprivacy` → alegeți botul → **Disable** (ca să vadă toate mesajele din grup).
2. Adăugați botul în **grupul sursă** și în **canalul LC boutique** ca administrator
   (în canal cu dreptul „Post messages").
3. **ID-urile**: porniți botul întâi doar cu `TELEGRAM_BOT_TOKEN` și `ANTHROPIC_API_KEY`. Scrieți `/id` în grupul
   sursă și în canal — botul răspunde cu ID-ul (ex. `-1001234567890`). Pentru un canal public puteți pune
   și direct `@numele_canalului` la `TARGET_CHAT_ID`.
4. **Cheia Claude**: [console.anthropic.com](https://console.anthropic.com) → API Keys.
5. Copiați `.env.example` în `.env` și completați valorile.
6. Porniți botul pe un server care rulează permanent (VPS, Railway, Render, Fly.io etc.):

```bash
# cu Docker
docker build -t lcboutique .
docker run -d --restart unless-stopped --env-file .env --name lcboutique lcboutique

# sau direct cu Python 3.11+
pip install -r requirements.txt
set -a; . ./.env; set +a
python -m lcboutique
```

## Teste

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Limitări

- Publicarea este pe canalul Telegram. Textul este scris să fie potrivit și pentru Instagram, dar postarea
  automată pe Instagram nu este inclusă (cere cont Business și aprobare Meta).
- Sunt preluate pozele și videoclipurile trimise ca media obișnuită; imaginile trimise ca „fișier" sunt ignorate.
- Produsele în așteptare (cele 60 s de grupare) se pierd dacă botul este repornit exact atunci.
