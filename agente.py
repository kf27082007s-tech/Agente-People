"""Interpreta pedidos dos usuários e executa ações do Google"""
import json #converter dados das ferramentas do Google em JSON
import os #interagir com o sistema operacional
import unicodedata #normalizar strings Unicode (data/hora)
from typing import cast


from dotenv import load_dotenv #carrega variáveis de ambiente do arquivo .env
from openai import OpenAI #interagir com a API do OpenAI
from openai.types.chat import ChatCompletionToolParam


from google_tools import autenticar_google, agendar_entrevista_com_meet, registrar_candidato_planilha #importa funções do módulo google_tools


#carregar a groq_api_key


load_dotenv()  # carrega variáveis de ambiente do arquivo .env


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
openai = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
) if GROQ_API_KEY else None
client = openai


# Definir o modelo e indicar o id da planilha
MODELO_GROQ = "openai/gpt-oss-20b"  # modelo open source da OpenAI (gratuito)
PLANILHA_ID = os.environ.get("PLANILHA_ID")  # id da planilha do Google Sheets (definido no arquivo .env)


# Criando as ferramentas do agente
FERRAMENTAS: list[ChatCompletionToolParam] = cast(
    list[ChatCompletionToolParam],
    [
        {
            "type": "function",
            "function": {
                "name": "agendar_entrevista_com_meet",
                "description": "Agendar uma entrevista no Google Calendar com link do Google Meet",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "nome_candidato": {"type": "string"},
                        "email_candidato": {"type": "string"},
                        "vaga": {"type": "string", "description": "Vaga relacionada à entrevista, se informada."},
                        "status": {"type": "string", "description": "Status do candidato; use Entrevista agendada quando não houver outro status informado."},
                        "data_hora_inicio": {
                            "type": "string",
                            "description": "Data e hora de início da entrevista no formato ISO 8601 (ex: 2023-07-01T10:00:00-03:00)."
                        },
                        "data_hora_fim": {
                            "type": "string",
                            "description": "Data e hora de término da entrevista no formato ISO 8601 (ex: 2023-07-01T11:00:00-03:00)."
                        },
                    },
                    "required": [
                        "nome_candidato",
                        "email_candidato",
                        "data_hora_inicio",
                        "data_hora_fim",
                    ],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "registrar_candidato_planilha",
                "description": "Registrar os dados do candidato na planilha do Google Sheets",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id_planilha": {
                            "type": "string",
                            "description": "ID da planilha do Google Sheets. Opcional se já estiver definido no ambiente."
                        },
                        "nome": {"type": "string"},
                        "email": {"type": "string"},
                        "vaga": {"type": "string"},
                        "status": {"type": "string"},
                        "data_hora_inicio": {
                            "type": "string",
                            "description": "Data e hora da entrevista em ISO 8601, se informadas.",
                        },
                        "data_hora_fim": {
                            "type": "string",
                            "description": "Horário de término em ISO 8601, se informado.",
                        },
                    },
                    "required": ["nome", "email"],
                },
            },
        },
    ],
)


PROMPT = (
    "Você é um assistente virtual que ajuda a agendar entrevistas e registrar candidatos. "
    "Você pode agendar entrevistas no Google Calendar com link do Google Meet e registrar os dados dos candidatos na planilha do Google Sheets. "
    "Sempre que o usuário pedir para marcar/agendar uma entrevista e informar nome e email, "
    "chame a ferramenta de agendamento com nome, email, vaga e horários disponíveis; "
    "o sistema registrará o candidato na planilha independentemente do resultado do convite. "
    "Se o usuário pedir apenas um cadastro, chame a ferramenta da planilha. "
    "Ao agendar uma entrevista, inclua vaga e status quando esses dados forem informados pelo usuário. "
    "Use as ferramentas disponíveis para realizar essas tarefas."
)


# Função para normalizar (tokenizar) os dados de entrada do usuario


def normalizar(texto):
    """Normalizar, removendo acentos e convertendo para minúsculas"""
    sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(letra for letra in sem_acento if not unicodedata.combining(letra)).lower()  # remove acentos e converte para minúsculas




# Função para executar agente de recrutamento


def executar_agente_rh(mensagem_usuario):
    """Executa o agente de recrutamento, interpretando a mensagem do usuário e chamando as funções apropriadas."""
    if client is None:
        return [("erro", "Configure chave de API do Groq para executar o agente de recrutamento.")]

    try:
        resposta = client.chat.completions.create(
            model=MODELO_GROQ,
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": mensagem_usuario},
            ],
            tools=FERRAMENTAS,
            tool_choice="auto",
        )
    except Exception as erro:
        return [("erro", f"Falha ao consultar a API do Groq: {erro}")]

    mensagem = resposta.choices[0].message
    tool_calls = mensagem.tool_calls or []

    if not tool_calls:
        return [("texto", mensagem.content or "Ops, não sei fazer isso.")]

    # Processa o agendamento primeiro para que o registro também receba data e hora.
    tool_calls = sorted(
        tool_calls,
        key=lambda chamada: getattr(getattr(chamada, "function", None), "name", None)
        != "agendar_entrevista_com_meet",
    )

    resultados = []
    candidatos_registrados = set()

    for chamada in tool_calls:
        funcao = getattr(chamada, "function", None)
        nome = getattr(funcao, "name", None)
        raw_args = getattr(funcao, "arguments", "{}") or "{}"

        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (TypeError, ValueError, json.JSONDecodeError):
            args = {}

        if nome == "agendar_entrevista_com_meet":
            nome_candidato = args.get("nome_candidato")
            email_candidato = args.get("email_candidato")
            chave_candidato = (
                str(nome_candidato or "").strip().lower(),
                str(email_candidato or "").strip().lower(),
            )

            # Salva primeiro: o convite pode ser recusado ou o Calendar pode falhar,
            # mas isso não deve bloquear o registro da solicitação na planilha.
            try:
                if not nome_candidato or not email_candidato:
                    raise ValueError("Dados do candidato incompletos: nome e email são obrigatórios.")

                resultado_planilha = registrar_candidato_planilha(
                    PLANILHA_ID,
                    nome_candidato,
                    email_candidato,
                    args.get("vaga") or "não informado",
                    args.get("status") or "Entrevista solicitada",
                    args.get("data_hora_inicio"),
                    args.get("data_hora_fim"),
                )
                candidatos_registrados.add(chave_candidato)
                resultados.append(("sheets", resultado_planilha))
            except Exception as erro:
                resultados.append(("erro", f"Não foi possível registrar o candidato na planilha: {erro}"))

            try:
                resultado_calendar = agendar_entrevista_com_meet(
                    nome_candidato,
                    email_candidato,
                    args.get("data_hora_inicio"),
                    args.get("data_hora_fim"),
                )
                resultados.append(("calendar", resultado_calendar))
            except Exception as erro:
                resultados.append(("erro", f"Candidato enviado à planilha, mas houve falha no Calendar: {erro}"))

        elif nome == "registrar_candidato_planilha":
            try:
                nome_candidato = args.get("nome")
                email_candidato = args.get("email")
                if not nome_candidato or not email_candidato:
                    raise ValueError("Dados do candidato incompletos: nome e email são obrigatórios.")

                chave_candidato = (str(nome_candidato).strip().lower(), str(email_candidato).strip().lower())
                if chave_candidato in candidatos_registrados:
                    continue

                resultado = registrar_candidato_planilha(
                    PLANILHA_ID,
                    nome_candidato,
                    email_candidato,
                    args.get("vaga", "não informado"),
                    args.get("status") or "Entrevista solicitada",
                    args.get("data_hora_inicio"),
                    args.get("data_hora_fim"),
                )
                resultados.append(("sheets", resultado))
                candidatos_registrados.add(chave_candidato)
            except Exception as erro:
                resultados.append(("erro", f"Erro ao registrar candidato: {erro}"))

        else:
            resultados.append(("erro", f"Ferramenta desconhecida: {nome}"))

    return resultados



