# === LangChain + Notion ブロック追記版 (ConversationBufferMemory + VectorStore統合) ===
'''
graph TD
    A[ユーザー入力] --> B{保存指示ありか}
    B -->|あり| C[ConversationBufferMemory から履歴取得]
    C --> D[履歴を要約 (LLM)]
    D --> E[Notion 固定ページにブロック追記]
    D --> F[要約内容を VectorStore に追加]
    B -->|なし| G[VectorStore で類似履歴検索]
    G --> H[ConversationBufferMemory の履歴と検索結果をプロンプトに挿入]
    H --> I[LLM 応答生成]
    I --> J[ユーザーに回答表示]
    A --> K[ConversationBufferMemory に発話追加]
    I --> K
'''

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain.vectorstores import FAISS
from langchain.memory import ConversationBufferMemory
from notion_client import Client
import os
from dotenv import load_dotenv
import pickle
import shutil

# === 環境変数読み込み ===
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ORGANIZATION_ID = os.getenv("OPENAI_ORGANIZATION_ID")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
PAGE_ID = os.getenv("PAGE_ID")
MEMORY_PATH = "memory.pkl"
VECTORSTORE_PATH = "vectorstore_index"

if not PAGE_ID:
    raise ValueError("PAGE_ID is not set in .env file")

# === NotionとLLMの初期化 ===
notion = Client(auth=NOTION_TOKEN)
llm = ChatOpenAI(
    api_key=OPENAI_API_KEY,
    organization=OPENAI_ORGANIZATION_ID,
    model_name="gpt-3.5-turbo"
)
embeddings = OpenAIEmbeddings()

# === Memoryの初期化 ===
def load_memory():
    if os.path.exists(MEMORY_PATH):
        with open(MEMORY_PATH, "rb") as f:
            return pickle.load(f)
    return ConversationBufferMemory(return_messages=True)

def save_memory(memory):
    with open(MEMORY_PATH, "wb") as f:
        pickle.dump(memory, f)

def refresh_memory():
    """会話履歴をリフレッシュする"""
    global memory
    memory = ConversationBufferMemory(return_messages=True)
    if os.path.exists(MEMORY_PATH):
        os.remove(MEMORY_PATH)
    print("🔄 会話履歴をリフレッシュしました")

memory = load_memory()

# === VectorStoreの初期化 ===
def load_vectorstore():
    """VectorStoreをロード、なければ新規作成"""
    if os.path.exists(VECTORSTORE_PATH):
        return FAISS.load_local(VECTORSTORE_PATH, embeddings, allow_dangerous_deserialization=True)
    
    # 初期化時は最低1つのテキストが必要
    vectorstore = FAISS.from_texts(
        ["初期化用のダミーテキストです。このテキストは検索には使用されません。"],
        embeddings
    )
    vectorstore.save_local(VECTORSTORE_PATH)
    return vectorstore

def save_vectorstore(store):
    """VectorStoreを保存"""
    store.save_local(VECTORSTORE_PATH)

def refresh_vectorstore():
    """VectorStoreをリフレッシュする"""
    global vectorstore
    # 初期化時は最低1つのテキストが必要
    vectorstore = FAISS.from_texts(
        ["初期化用のダミーテキストです。このテキストは検索には使用されません。"],
        embeddings
    )
    if os.path.exists(VECTORSTORE_PATH):
        try:
            shutil.rmtree(VECTORSTORE_PATH)
        except Exception as e:
            print(f"⚠️ VectorStoreの削除中にエラーが発生しました: {e}")
            return
    print("🔄 VectorStoreをリフレッシュしました")

def refresh_all():
    """会話履歴とVectorStore両方をリフレッシュする"""
    refresh_memory()
    refresh_vectorstore()
    print("✨ 全てのデータをリフレッシュしました")

vectorstore = load_vectorstore()

# === Notionブロック追記関数 ===
def append_to_page(page_id, content):
    notion.blocks.children.append(
        block_id=page_id,
        children=[
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {"type": "text", "text": {"content": content}}
                    ]
                }
            }
        ]
    )
    print(f"📝 ページに追記しました: {content}")

# === 保存指示判定 ===
def is_valid_save_command(user_input):
    if "保存" not in user_input:
        return False

    prompt = f"""
    以下のユーザー発話は、AIとの議論内容をNotionに保存するように依頼している意図がありますか？
    「はい」または「いいえ」で答えてください。

    発話: "{user_input}"
    """
    decision = llm.invoke(prompt).content
    #print(f"判断結果: {decision}")
    return "はい" in decision

# === 実行例 ===
if __name__ == "__main__":
    try:
        while True:
            user_input = input("> ")
            if user_input.lower() in ["exit", "quit"]:
                break
            elif user_input.lower() in ["refresh", "clear"]:
                refresh_all()
                continue
            elif user_input.lower() == "refresh memory":
                refresh_memory()
                continue
            elif user_input.lower() == "refresh vectorstore":
                refresh_vectorstore()
                continue

            if is_valid_save_command(user_input):
                print("💾 保存指示が検出されました。指定ページに追記します。")
                summary_prompt = """
                これまでの議論内容をNotionに保存するのに適した形で要約してください。
                特に、質疑応答を重視して、[質問]→[回答]の形式で示すようにしてください。
                """
                history = memory.load_memory_variables({})["history"]
                # リストを文字列に変換してから連結
                messages = "\n".join([msg.content for msg in history]) + f"\n{summary_prompt}"
                print(f"要約プロンプト: {messages}")
                summary = llm.invoke(messages).content

                # Notion保存
                append_to_page(PAGE_ID, summary)

                # VectorStore登録
                vectorstore.add_texts([summary])
                print("🔄 VectorStoreに要約を追加しました。")
                
                try:
                    save_vectorstore(vectorstore)
                except Exception as e:
                    print(f"⚠️ VectorStoreの保存に失敗しました: {e}")

                #memory.chat_memory.add_ai_message(summary)

            else:
                memory.chat_memory.add_user_message(user_input)
                # 関連履歴検索 (省略可)
                user_query = user_input
                docs = vectorstore.similarity_search(user_query, k=2)
                retrieved = "\n".join([d.page_content for d in docs])

                # LLM応答
                prompt = f"これまでの会話: {memory.load_memory_variables({})['history']}\n\n関連議論: {retrieved}\n\nユーザー: {user_input}"
                result = llm.invoke(prompt).content
                print(result)
                memory.chat_memory.add_ai_message(result)

    finally:
        save_memory(memory)
        print("✅ 会話履歴を保存しました。")
