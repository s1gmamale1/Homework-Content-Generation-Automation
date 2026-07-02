This is a direct content generation task with a complete specification already provided — the brainstorming skill doesn't apply here. Generating the content now.

# Haqiqiy Hayot Sinovi — Kanal Tarmog'idagi Umumiy Oqim

## Rol

Siz **"Toshkentsuvloyiha" muassasasining gidravlik hisob-tahlil bo'limida ishlaydigan tizimlar muhandisisiz** — suv ta'minot tarmoqlarini algebraik modellar orqali hisoblash va loyihalash sizning asosiy vazifangiz. Bugun loyiha rahbari parallel ulangan ikki kanal segmentining birlashgan oqim formulasini soddalashtirish va x = 3 bosim parametrida aniq qiymat topishni topshirdi.

## Vazifa

Ikkita parallel kanal segmentining umumiy oqimi Q = Q₁ + Q₂ ni ifodalovchi algebraik kasrlar yig'indisini soddalashtiring va x = 3 bosim parametrida son qiymatini aniqlang.

## Holat

Loyiha texnik hujjatlarida birinchi kanal segmenti uchun oqim tezligi Q₁ = 1/[x(x−1)] (m³/soat), ikkinchi kanal segmenti uchun Q₂ = 3/(x²−1) (m³/soat) sifatida berilgan — bu yerda x tizim bosimiga bog'liq o'lchamsiz parametr (x > 1). Segmentlarning quvur diametrlari mos ravishda 150 mm va 200 mm bo'lib, qurilish smetasi uchun zarur ma'lumot sifatida keltirilgan. Muhandislar xodimi: soddalashtirilgan umumiy oqim formulasini va x = 3 dagi aniq son qiymatini taqdim eting.

## Bashorat

Hisoblashdan oldin: agar x = 3 ni algebraik soddalashtirishsiz to'g'ridan-to'g'ri Q₁ va Q₂ ga qo'ysangiz, natija qanchalik qulay ko'rinishda chiqadi? Algebraik soddalashtirishdan keyin ifoda qisqaradimi yoki murakkablashadimi? O'z kutganingizni bir jumlada yozing.

---

### Qaror 1 — Maxrajning ichki tuzilishi

EKUK topishdan oldin ikkita maxrajni tekshirasiz: Q₁ ning maxraji **x(x−1)**, Q₂ ning maxraji **x²−1**. Bu maxrajlar bilan ishlashdan avval qanday qadam tashlaysiz?

**Variantlar:**

- **A)** Ikkala maxraj tayyor ko'rinishda — EKUK = x(x−1) · (x²−1). To'g'ridan-to'g'ri ko'paytiramiz.
- **B)** x²−1 ni avval ko'paytuvchilarga ajratish kerak: x²−1 = (x−1)(x+1). Keyin har ikki maxrajdagi ko'paytuvchilarni taqqoslaymiz. *(To'g'ri)*
- **C)** x²−1 kattaroq maxraj — uni umumiy maxraj sifatida olamiz; x(x−1) unga bo'linishi mumkin.
- **D)** Ikkala maxrajda x ishtirok etadi — EKUK = x.

**Nima uchun?** Tanlagan variantingizni 1–2 jumlada asoslang: nima uchun aynan shu qadam EKUK ni to'g'ri topishga xizmat qiladi?

*To'g'ri mulohaza nimalarga tayangan bo'lishi kerak: x²−1 to'liq kvadrat ayirmasi ekanligini ko'rish — x²−1 = (x−1)(x+1) — va ko'paytuvchilarni taqqoslashdan avval har bir maxrajni ajratish zarurligi.*

**Ishonch darajangiz:** Ishonaman / Ehtimol / Taxmin

---

**To'g'ri javob uchun:** Aynan shu ko'rish hamma narsani hal qiladi. x²−1 = (x−1)(x+1) deb yozilgach, ikkala maxrajda (x−1) umumiy ko'paytuvchi sifatida ko'rinib turadi — uni bir marta hisoblaymiz. Tizim muhandislari doim maxrajlarning ichki tuzilishini avval tekshiradi: to'g'ridan-to'g'ri ko'paytirish formulani keraksiz murakkablashtiradi va soddalashtirish imkonini yo'qotadi.

**Qisman to'g'ri:** Ajratish kerakligini sezdingiz — bu to'g'ri qadam. Ammo *nima uchun* x²−1 ni ajratish shart ekanligini — ikkala maxrajdagi umumiy ko'paytuvchi (x−1) ni bir marta olish uchun — aniq ifodalamadingiz. Keyingi bosqichda EKUK ni noto'g'ri topish ehtimoli yuqori bo'lib qoladi.

**Noto'g'ri javob uchun:** Hali emas. A variantdagi yondashuv noto'g'ri EKUK hosil qiladi — x²−1 = (x−1)(x+1) bo'lgani uchun x(x−1)·(x²−1) da (x−1) omili ikki marta hisoblangan bo'ladi. Savol: x²−1 ifodasi a²−b² formulasiga o'xshamayaptimi? Uni (a−b)(a+b) ko'rinishida yozish mumkinmi?

---

### Qaror 2 — EKUK ni aniqlash

x²−1 = (x−1)(x+1) ekanligini aniqladingiz. Endi ko'paytuvchilarni taqqoslab, to'g'ri EKUK ni toping:

- Q₁ ning maxraji: **x · (x−1)**
- Q₂ ning maxraji: **(x−1) · (x+1)**

**Variantlar:**

- **A)** EKUK = x · (x−1) · (x+1) *(To'g'ri)*
- **B)** EKUK = x · (x−1) · (x²−1) — ikkala asl maxrajni butunligicha ko'paytirish kerak
- **C)** EKUK = x · (x²−1) — birinchi maxrajdan x olinadi, ikkinchi maxraj butunligicha saqlanadi
- **D)** EKUK = (x−1) — ikkala maxrajdagi yagona umumiy ko'paytuvchi yetarli

**Nima uchun?** EKUK ni topishda qanday mezon ishlatdingiz — ko'paytuvchilarni taqqoslash tartibini tushuntiring.

*To'g'ri mulohaza nimalarga tayangan bo'lishi kerak: takrorlanuvchi (x−1) bir marta olinadi; x va (x+1) alohida-alohida bir marta qo'shiladi; EKUK = x(x−1)(x+1) — bu haqiqatan eng kichik.*

**Ishonch darajangiz:** Ishonaman / Ehtimol / Taxmin

---

**To'g'ri javob uchun:** To'g'ri. x(x−1) da — x va (x−1); (x−1)(x+1) da — (x−1) va (x+1). Umumiy (x−1) bir marta olinadi, qolgan x va (x+1) qo'shiladi: EKUK = x·(x−1)·(x+1). B variantidagi EKUK bundan (x−1) marta katta, ya'ni ortiqcha omil bor — bu "eng kichik umumiy karrali" emas. Endi ikkala kasrni to'g'ri qo'sha olasiz.

**Qisman to'g'ri:** EKUK to'g'ri topildi, lekin qaysi ko'paytuvchi takrorlanuvchi ekanligini va uni nima uchun bir marta olganingizni — "eng kichik" kafolat qanday ta'minlanadi — ochiq ifodalamadingiz. Hisob to'g'ri bo'lsa ham, asoslash chuqurroq bo'lishi kerak.

**Noto'g'ri javob uchun:** Hali emas. B variantni tekshiraylik: x(x−1)·(x²−1) = x(x−1)·(x−1)(x+1) = x·(x−1)²·(x+1). Ko'rinyaptimi — (x−1) ikki marta kirib qoldi? Savol: "eng kichik" umumiy karrali qanday ta'minlanadi — har bir ko'paytuvchi necha marta olinishi kerak?

---

### Qaror 3 — Qo'shimcha ko'paytuvchilar va yakuniy qiymat

EKUK = x(x−1)(x+1) aniqlandi. Endi har bir kasr uchun qo'shimcha ko'paytuvchini toping, suratlarni ko'paytiring, birlashtiring va x = 3 da umumiy oqimni hisoblang.

Eslatma: Q₂ ning maxraji endi (x−1)(x+1) — ya'ni x²−1 ajratilgan ko'rinishda.

**Variantlar:**

- **A)** Q₁ uchun qo'shimcha ko'paytuvchi = (x+1); Q₂ uchun = x. Surat: 1·(x+1) + 3·x = (x+1+3x) = (4x+1). Formula: (4x+1)/[x(x²−1)]. x = 3 da: **13/24** m³/soat. *(To'g'ri)*
- **B)** Q₁ uchun qo'shimcha ko'paytuvchi = (x+1); Q₂ uchun = x. Lekin Q₂ ning surati ko'paytirilmaydi: surat = (x+1)+3 = x+4. Formula: (x+4)/[x(x²−1)]. x = 3 da: **7/24** m³/soat.
- **C)** Q₁ uchun qo'shimcha ko'paytuvchi = (x−1) (noto'g'ri aniqlangan); Q₂ uchun = x(x+1). Surat: (x−1)+3x(x+1) — soddalashtirib bo'lmaydi, natija noto'g'ri.
- **D)** Algebraik soddalashtirishni o'tkazib, to'g'ridan-to'g'ri x = 3 qo'yiladi: Q₁ = 1/(3·2) = 1/6; Q₂ = 3/8. Yig'indi: 4/24 + 9/24 = **13/24** m³/soat.

**Nima uchun?** A va D ikkisi ham 13/24 beradi. Kasbiy muhandislik hisob-kitobida qaysi yondashuv afzal va nima uchun? 1–2 jumlada tushuntiring.

*To'g'ri mulohaza nimalarga tayangan bo'lishi kerak: algebraik soddalashtirilgan formula (4x+1)/[x(x²−1)] bir marta chiqariladi va har xil x uchun qayta ishlatiladi; to'g'ridan-to'g'ri qo'yish har safar boshidan hisoblashni talab qiladi.*

**Ishonch darajangiz:** Ishonaman / Ehtimol / Taxmin

---

**To'g'ri javob uchun (A):** Professional yondashuv — aynan shunday. (4x+1)/[x(x²−1)] formulasi bir marta chiqarildi, x = 3 qo'yildi: 13/(3·8) = 13/24 m³/soat. Bu formula qayta ishlatiladi — x = 4 bo'lsa 17/60, x = 5 bo'lsa 21/100. Muhandis formulani bir marta yozadi, keyin faqat x ni almashtiradi.

**Qisman to'g'ri (D):** Son javob to'g'ri — 13/24. Lekin D yondashuvi soddalashtirilgan formulasiz tugaydi: loyihada x o'zgarsa, yana boshidan hisoblash kerak bo'ladi. Algebraik soddalashtirishning maqsadi — qayta ishlatiladigan formula olish, nafaqat bir martalik son.

**Noto'g'ri javob uchun (B yoki C):** Hali emas. B da Q₂ ning surati 3 bo'lib qolgan — holbuki qo'shimcha ko'paytuvchi x bo'lgani uchun 3·x = 3x yozilishi kerak edi. Savol: qo'shimcha ko'paytuvchi faqat maxrajgami, yoki surat va maxraj ikkalasigami ko'paytiriladi?

---

## Yakuniy Xulosa

**Muhandis nima qilgan bo'lar edi:**
Birinchi navbatda x²−1 = (x−1)(x+1) deb ajratib, ikkala maxrajda (x−1) umumiy ekanligini ko'rgan bo'lar edi. EKUK = x(x−1)(x+1) aniqlangan bo'lar; qo'shimcha ko'paytuvchilar — Q₁ uchun (x+1), Q₂ uchun x — topilgan bo'lar edi. Suratlar birlashtirilib soddalashtirilgach (4x+1)/[x(x²−1)] formulas hosil bo'lar, x = 3 qo'yilsa: **13/24 m³/soat**.

**Kuchli mulohaza nimaga tayangan:**
Ko'phad maxrajni ko'paytuvchilarga ajratish → takroriy ko'paytuvchini bir marta olish → haqiqiy EKUK = x(x−1)(x+1) → qo'shimcha ko'paytuvchi = EKUK ÷ maxraj → suratni ko'paytirish → birlashtirish va soddalashtirish → keyin qiymat qo'yish. Bu §4 ning to'liq amal tartibi.

**Ko'pchilik adashgan joylar:**
- x²−1 ni ajratmasdan to'g'ridan-to'g'ri ko'paytirish — (x−1) ikki marta hisoblaniб, soddalashtirib bo'lmaydigan murakkab ifoda hosil bo'ladi.
- Q₂ ning suratini qo'shimcha ko'paytuvchiga ko'paytirmaslik — "3" ni "3x" qilmaslik — surat noto'g'ri chiqadi.
- Algebraik soddalashtirishni o'tkazib, to'g'ridan-to'g'ri qiymat qo'yish — bir martalik to'g'ri son berilsa ham, qayta ishlatiladigan formula qo'lga kiritilmaydi.

**Qizil seld:**
Quvurlarning diametrlari — 150 mm va 200 mm. Bu ma'lumot real gidravlik hisoblashlarda muhim, lekin berilgan algebraik model (Q₁ va Q₂ formulalari) faqat x bosim parametriga bog'liq. Muhandis bu ma'lumotni "Keyingi bosqich uchun — hozirgi algebraik model diametrni ko'zmaydi" deb ataylab chetga qo'ygan bo'lar edi.
