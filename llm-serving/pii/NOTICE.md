# PII NER 모델 — 서드파티 출처·라이선스 고지 (NOTICE)

본 PII 가드는 비정형 PII(이름/주소/조직 등) 탐지를 위해 아래 외부 NER 모델을
추론에 사용한다. 각 모델·학습데이터의 라이선스(CC-BY / CC-BY-SA / MIT)에 따른
**출처표기(attribution) 의무를 본 문서로 이행**한다.

> 모델 가중치는 `/models/PII/` 에 보관하며 본 저장소에는 포함하지 않는다.
> 라이선스는 모두 1차 출처(LICENSE 원문 / Zenodo API / 모델카드)로 확인했다.

---

## 1. `vmaca123/korean-pii-ner-v3` — 이름/주소/조직 NER

| 항목 | 내용 |
|------|------|
| 용도 | 비정형 PII(NAME/ADDRESS/ORG) 정밀 탐지 |
| 모델 라이선스 | **CC-BY-SA-4.0** (KLUE base 상속) |
| 저작자 | Kim, Minwoo (`vmaca123`) |
| base 모델 | `klue/roberta-large` |
| 학습 데이터 | KLUE-NER train (CC-BY-SA-4.0) + 자체 합성(Faker-ko) |
| URL | https://huggingface.co/vmaca123/korean-pii-ner-v3 |

인용(BibTeX):

```bibtex
@misc{kimminwoo2026koreanpiinerv3,
  title  = {Korean PII NER v3: klue/roberta-large fine-tuned for PII guardrails},
  author = {Kim, Minwoo},
  year   = {2026}
}
```

---

## 2. `townboy/kpfbert-kdpii` — 광범위 PII NER (대화체, 33라벨)

| 항목 | 내용 |
|------|------|
| 용도 | 광범위 PII(이름/주소/조직/전화/계좌/이메일/생년월일 등) 안전망 |
| 모델 라이선스 | **미선언**(업로더가 별도 표기 없음) — 단 구성요소가 모두 상업 허용 |
| base 모델 | KPF-BERT / KPF-BERT-NER — **MIT** (© 2021 KPFBERT) |
| 학습 데이터 | KDPII (`연대1_PII_dataset_V3`) — **CC-BY-4.0** |
| 데이터 저작자 | 연세대학교 김한샘 연구실(HamSaeM Kim's Lab) + TSCIENTIFIC Co., Ltd |
| 데이터 논문 | Li Fei et al., "KDPII", IEEE Access, 2024 |
| URL | https://huggingface.co/townboy/kpfbert-kdpii |
| base LICENSE | https://github.com/KPFBERT/kpfbert (MIT) |
| 데이터 출처 | https://zenodo.org/records/10968609 (CC-BY-4.0) |

---

## 준수 사항

- **출처표기(CC-BY / CC-BY-SA / MIT 저작권 고지)**: 본 문서로 이행. 외부 공개
  문서/서비스 notice에도 동일 고지를 포함한다.
- **ShareAlike(vmaca CC-BY-SA-4.0)**: 내부 서비스에서 모델로 **추론만** 하는 것은
  '배포(share)'가 아니므로 ShareAlike가 트리거되지 않는다. 모델 **가중치 자체를
  수정·재배포**할 경우에만 동일 라이선스(CC-BY-SA-4.0) 적용 의무가 발생한다.
- **상업적 사용**: base(MIT)·데이터(CC-BY-4.0/CC-BY-SA-4.0) 모두 상업적 사용을
  허용하므로 사내 서비스 적용에 차단 요인은 없다.
- **`townboy` 모델 자체 라이선스 미선언**: 금지가 아니라 '명시 부재'이며 상류
  구성요소(base+데이터)가 모두 permissive다. 보수적 확정이 필요하면 업로더에게
  라이선스 확인을 요청하거나, 동일 데이터(CC-BY-4.0)·동일 base(MIT)로 사내
  재학습하여 권리관계를 자체 확정할 수 있다.
