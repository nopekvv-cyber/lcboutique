"""Analiza postării producătorului cu Claude (poze + text → date structurate)."""

from __future__ import annotations

import base64
import logging

import anthropic

from .models import PROFIT_RANGES, TOPICS, Analysis

log = logging.getLogger(__name__)

_RANGES = "\n".join(
    f"- {cat} ({TOPICS[cat]}): {lo}–{hi} lei" for cat, (lo, hi) in PROFIT_RANGES.items() if cat != "haine_pe_loc"
)

SYSTEM_PROMPT = f"""Ești asistentul LC boutique, un magazin online de haine pentru femei.
Primești postarea unui producător (de obicei în ucraineană sau rusă): fotografii numerotate #0, #1, ... și textul.
Proprietara a aprobat deja produsul; tu extragi datele pentru postarea de vânzare în limba română.

Reguli:
1. Identifică fiecare produs distinct. Dacă postarea conține mai multe produse (coduri diferite),
   returnează câte un element pentru fiecare și nu amesteca datele între ele. Variantele de culoare
   ale aceluiași cod sunt UN singur produs cu mai multe culori.
2. `code`: codul/modelul/articolul EXACT, copiat caracter cu caracter (arт./модель/код). Nu îl traduce,
   nu îl modifica. Dacă nu există cod, lasă gol.
3. Nu inventa nimic. Mărimi, culori, materiale, compoziție, măsurători: doar ce scrie producătorul
   (sau se vede fără dubiu în text). Ce nu e specificat rămâne gol. Poți folosi pozele doar pentru a
   înțelege tipul produsului și croiala, nu pentru a ghici materialul sau mărimile.
4. `sizes`: păstrează mărimile exact ca la producător (ex. „42-44, 46-48" sau „S, M, L" sau „універсал" → „universală").
5. Traducere naturală, nu mot-à-mot. `title`: denumire scurtă și elegantă (ex. „Rochie midi din tricot").
   `description`: 1–3 propoziții, clar, feminin, ușor de citit, potrivit pentru Telegram și Instagram,
   fără fraze inutile și fără exagerări. Menționează natural cele mai importante caracteristici
   (croiala, lungimea, căptușeala etc.), pentru că postarea nu are o listă separată de detalii.
   `details`: detalii utile clientei (croială, lungime, căptușeală, fermoar/nasturi, buzunare, talie,
   elastic, glugă, centură, înălțimea modelului din poză etc.), fiecare ca o frază scurtă.
6. NU include nicăieri: telefoane, adrese, conturi Telegram/Instagram, nume de manageri, informații
   despre depozit, stoc sau livrare ale producătorului, și niciun preț.
7. `prices`: toate prețurile producătorului pentru ACEST produs, cu tipul lor
   (дроп = drop, опт/оптова = opt, роздріб/розниця/РРЦ = retail, un singur preț fără tip = unspecified)
   și moneda (грн/₴/uah = UAH; $/usd/дол./долар = USD). Nu converti nimic.
8. `profit_lei`: alege profitul LC boutique din intervalul categoriei, în funcție de prețul inițial,
   material, complexitatea modelului și aspectul produsului (mai ieftin/simplu → spre minim,
   mai scump/elaborat → spre maxim):
{_RANGES}
9. `category`: topicul din grupul LC boutique în care se publică produsul (cel mai potrivit, unul singur):
   - rochii de zi cu zi → rochite; rochii de seară/ocazie/evening → rochii_elegante;
   - costume (sacou/bluză + pantaloni/fustă, compleuri) → costume; costume sportive/trening → costume_sport;
     costume office/cu sacou clasic → costume_clasice; costume din tricot → costume_tricotate;
   - geci, scurte, trenciuri, bomber, puf → scurte_trenciuri; paltoane → paltoane; veste → jalete;
   - blugi și pantaloni → pantaloni; fuste și șorți → fustite_sorti; pulovere/hanorace → pulovere;
   - malete (longsleeve), body → malete_body; topuri, corsete → topuri_corsete; maiouri → maiouri;
   - bluze → bluzite; cămăși → camasi; costume de baie/plajă → plaja; salopete → salopete;
   - marimi_mari DOAR dacă producătorul prezintă produsul explicit ca mărimi mari/plus size (батал);
   - barbati DOAR pentru haine bărbătești.
10. `photo_indices`: pozele care aparțin fiecărui produs. Dacă e un singur produs, toate pozele.
11. `notes`: scrie scurt, în română, doar dacă ceva lipsește sau e neclar (ex. lipsește codul sau prețul).
"""


class AnalysisError(Exception):
    pass


class Analyzer:
    def __init__(self, model: str, effort: str) -> None:
        self.client = anthropic.AsyncAnthropic()
        self.model = model
        self.effort = effort

    async def analyze(self, text: str, photos: list[bytes]) -> Analysis:
        content: list[dict] = []
        for i, data in enumerate(photos):
            content.append({"type": "text", "text": f"Fotografia #{i}:"})
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(data).decode("ascii"),
                    },
                }
            )
        content.append(
            {
                "type": "text",
                "text": "Textul producătorului:\n\n" + (text.strip() or "(fără text)"),
            }
        )

        request = dict(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_format=Analysis,
            messages=[{"role": "user", "content": content}],
        )
        try:
            if self.model.startswith("claude-haiku"):
                # Haiku: model rapid și ieftin, fără thinking/effort
                response = await self.client.messages.parse(**request)
            else:
                response = await self.client.beta.messages.parse(
                    **request,
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.effort},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
        except anthropic.RateLimitError as exc:
            raise AnalysisError("limita API Claude a fost atinsă, încercați din nou peste un minut") from exc
        except anthropic.APIStatusError as exc:
            raise AnalysisError(f"eroare API Claude ({exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AnalysisError("nu mă pot conecta la API-ul Claude") from exc

        if response.stop_reason == "refusal":
            raise AnalysisError("Claude a refuzat să proceseze această postare")
        if response.stop_reason == "max_tokens":
            raise AnalysisError("răspunsul a fost trunchiat (postare prea lungă)")
        if response.parsed_output is None:
            raise AnalysisError("răspunsul nu a putut fi interpretat")
        log.info("Claude usage: %s", response.usage)
        return response.parsed_output
