# Fundamentos CVM – Agente de Análise Fundamentalista (TCC)

Este repositório gera o arquivo `fundamentos_cvm.json` com as demonstrações financeiras
anuais das companhias abertas brasileiras, a partir dos **dados abertos da CVM**
(DFP – Demonstrações Financeiras Padronizadas e FCA – Formulário Cadastral).

- `gerar_fundamentos.py`: baixa os arquivos da CVM e extrai as contas usadas no cálculo dos indicadores.
- `.github/workflows/atualizar.yml`: executa o programa automaticamente todo dia 5 de cada mês.
- `fundamentos_cvm.json`: resultado (valores em reais; exercício `t` e exercício anterior `t1`).

Cada atualização fica registrada no histórico de commits, o que permite identificar
exatamente qual versão da base foi usada em cada análise.

Fonte: Comissão de Valores Mobiliários – Portal de Dados Abertos (https://dados.cvm.gov.br).
