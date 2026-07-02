# Error Detection — spot the broken block, type the correction — Mathematics / Algebra (Matematika / Algebra)

## Concepts
- Algebraik kasrlarni qisqartirish
- Qisqa koʻpaytirish formulalari (kvadratlar ayirmasi)
- Qarama-qarshi ifodalarni qisqartirish (`a-b` vs `b-a`)

## The blocks
Quyida algebraik kasrni soddalashtirish bosqichlari keltirilgan. Ulardan biri xato.

1.  **Dastlabki ifoda:**
    `(2a - 2b) / (b² - a²) `
2.  **Suratni koʻpaytuvchiga ajratamiz:**
    `2(a - b) / (b² - a²) `
3.  **Maxrajni formulaga koʻra koʻpaytuvchilarga ajratamiz:**
    `2(a - b) / ( (b - a)(b + a) )`
4.  **Ifodalarni tahlil qilamiz:**
    Suratdagi `(a-b)` va maxrajdagi `(b-a)` oʻzaro qarama-qarshi ifodalardir.
5.  **Umumiy koʻpaytuvchini qisqartiramiz:**
    `(a-b)` va `(b-a)` qisqartirilganda `1` beradi, shuning uchun kasr quyidagicha boʻladi:
    `2 / (b + a)`
    (This is the broken block)
6.  **Yakuniy natija:**
    `2 / (a + b)`

## The correct version
`(a-b)` va `(b-a)` qisqartirilganda `-1` beradi, shuning uchun kasr quyidagicha boʻladi:
`-2 / (b + a)`

*Shu kabi javoblar qabul qilinadi: "(a-b) va (b-a) nisbati -1 ga teng", "Qisqartirganda -1 qoladi", "Kasrning ishorasi manfiy bo'lishi kerak". Asosiysi, talaba -1 koeffitsiyentini to'g'ri aniqlashi kerak.*

## The real mistake
Qarama-qarshi ifodalarni (`a-b` va `b-a` kabi) qisqartirganda, ularning nisbati `1` emas, balki `-1` ga teng ekanligini e'tibordan chetda qoldirish. Talabalar koʻpincha ifodalarning tashqi oʻxshashligiga aldanib, ishoralar farqini unutishadi.

## Hint
Surat va maxrajdagi bir-biriga "o'xshash" ko'paytuvchilarni diqqat bilan solishtiring. `(a-b)` ifodasi `(b-a)` ga chindan ham tengmi? Ular orasida qanday bog'liqlik bor?

## Why prompt
Bu blok nega notoʻgʻri edi?

*Toʻgʻri tushuntirish quyidagi fikrlarni oʻz ichiga olishi kerak: 5-blokdagi xulosa notoʻgʻri, chunki `(a-b)` va `(b-a)` qarama-qarshi ifodalardir. Ular `(a-b) = –(b-a)` munosabati orqali bogʻlangan. Shu sababli, ularni bir-biriga boʻlganda (qisqartirganda) nisbat `1` emas, `-1` ga teng boʻladi. Natijada butun kasrning ishorasi manfiyga oʻzgarishi va toʻgʻri javob `-2 / (b + a)` boʻlishi kerak edi.*

## Correct feedback
Toʻgʻri! Siz xatoni topdingiz va tuzatdingiz. Qarama-qarshi ifodalarni qisqartirganda `-1` koeffitsiyenti paydo boʻlishini unutmaslik muhim.

## Wrong-correction feedback
Bu toʻliq toʻgʻri emas. Yana bir bor urinib koʻring. Mana bir ishora: Surat va maxrajdagi bir-biriga "o'xshash" ko'paytuvchilarni diqqat bilan solishtiring. `(a-b)` ifodasi `(b-a)` ga chindan ham tengmi? Ular orasida qanday bog'liqlik bor?

## Reveal
**Xato blok:** `(a-b)` va `(b-a)` qisqartirilganda `1` beradi...
**Toʻgʻri shakli:** `(a-b)` va `(b-a)` qisqartirilganda `-1` beradi...
**Sababi:** `a-b = –(b-a)` boʻlgani uchun ularning nisbati `–1` ga teng.
