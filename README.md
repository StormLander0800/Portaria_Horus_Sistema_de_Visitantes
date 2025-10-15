# Portaria_Horus_Sistema_de_Visitantes
**Portaria HÓRUS — Sistema de Gestão de Visitantes e Fornecedores: controla cadastros, acessos e registros com base na LGPD, garantindo rastreabilidade, integridade e confidencialidade das informações, com auditoria e relatórios para segurança institucional.**


O que faz
- **Visitantes/Fornecedores**: cadastro, entrada/saída, foto na hora (retenção 90 dias), pesquisa por nome/CPF/RG.
- **Home**: KPIs e atalhos.
- **Alunos**: cadastrar manualmente, **import CSV** (upsert: matrícula), chegada em portaria com **cálculo automático de atraso** (base `HORUS_BASE_CLASS_TIME`, padrão 07:30).
- **Relatórios**: filtro por período, **export CSV** de visitas e atrasos de alunos.

## Rodar local
```
$env:HORUS_TZ="America/Sao_Paulo"
$env:HORUS_BASE_CLASS_TIME="08:00"
pip install -r requirements.txt
python app.py
```
Acesse: `http://127.0.0.1:5000` (câmera funciona em HTTPS ou `http://localhost`).

## Import de alunos (CSV)
Cabeçalhos obrigatórios: `matricula,nome_completo,turma`.

## Variáveis
- `HORUS_BASE_CLASS_TIME` — HH:MM (ex.: `08:00`).

## Estrutura
```
horus_visitors_alunos_v2/
├── app.py
├── horus.db            # gerado no primeiro run
├── photos/             # fotos (apagadas após 90 dias)
├── requirements.txt
├── README.txt
├── static/
│   └── style.css
└── templates/
    ├── base.html
    ├── home.html
    ├── index.html
    ├── search.html
    ├── students_home.html
    ├── students_new.html
    ├── students_import.html
    ├── students_checkin.html
    └── reports.html
```
