# App guide

This page is for the developers of an app that uses xat-rag. xat-rag returns ranked chunks of text. The app does the rest. The app fits the chunks to its LLM, builds the prompt, escapes the text and checks the citations. This page describes these tasks. The [API reference](api.md) lists the fields of each response.

## 1. Count the tokens and choose the chunks

Count the tokens with the tokenizer of your LLM. Do not use the `size_tokens` value for this. That value comes from the size ruler of xat-rag. The ruler has no link to your LLM, so its count can differ from your count.

Count the `header` and the `text` of a chunk together. The index holds both, so the LLM sees both.

Choose the chunks in your own code. A simple policy works well:

1. Take the chunks in rank order, best first.
2. Add a chunk when it fits the budget that is left for chunks.
3. Skip a chunk that does not fit. Go on to the next chunk.
4. Stop when the budget is full.

Count the system message, the question and the space for the answer too. The budget for chunks is what is left after these parts.

A result can have fewer chunks than you asked for. Plan for a short list. An empty list is possible when the RAGs hold no chunks.

## 2. Build the prompt

xat-rag returns no messages and no system prompt. Write them in your code.

- Put your instructions in the system message.
- Put the chunks and the question in the user message.
- Ask for a fixed answer format. For example, ask for JSON with an `answer` field and a `citations` field. A fixed format makes the answer easy to check.

## 3. Escape the untrusted text

Treat these values as untrusted:

- `text`
- `header`
- `title`
- `metadata` values

A JIRA comment, a diff or a document can hold text that tries to give the LLM new instructions. This is called prompt injection. xat-rag does not change the text. Your app must do this work.

Follow these rules:

- Wrap each chunk in a clear block. Use a tag, a fence or a label that the text cannot close.
- Escape the text before you wrap it. If you use XML-like tags, replace `&`, `<` and `>` in the text. For example, `&` becomes `&amp;`, `<` becomes `&lt;` and `>` becomes `&gt;`.
- Break the control patterns of your model. Some models read text such as `[INST]` or `<|im_start|>` as a control token. Change such text before you send it.
- Say in the system message that the chunk text is data, not instructions.
- Escape the metadata values with the same rules. Numbers and booleans need no escape.

Here is a small example for XML-like blocks:

```python
def escape_for_block(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
```

Do not run commands from the answer. Do not open links from the answer without a user action. The answer is untrusted text too.

## 4. Handle citations

xat-rag does not check citations. Your app does this check.

- Give each chunk a short label in your code, for example `C1`, `C2` and so on.
- Keep a map from each label to the `chunk_id` of the chunk. Keep the `source` of the chunk too.
- Ask the model to cite labels, not chunk IDs.
- Reject a label that you did not send. Reject an answer that is not valid JSON.
- Use `source.uri` to open the origin of a chunk. A PDF `uri` ends with `#page=N`. Use the `span` to show the lines, the pages or the blocks.
- Use `source.version` to see if the document changed after the answer was made.

xat-rag keeps no record of past requests. Store the `chunk_id`, the `source` and the `span` of each chunk that you show to the user, when you need them later.

## 5. Prepare the question and the documents

xat-rag limits the size of the question for each search stage. Prepare long or noisy text before you ask.

- Keyword search uses at most 32 terms of the question. Terms after the first 32 are not used for keyword search.
- Vector search keeps only the part of the question that fits the embedder input, which is 512 model tokens. The end of a long question is not used.
- The reranker reads only the first 600 characters of the question.
- Put the most important text first. Remove long pasted output that does not help the search.
- The question can have at most 20000 characters. The question must have text after control characters are removed.

The chunker splits a long section. Each chunk fits the `max_chunk_tokens` value of its source, counted with the size ruler. See the [architecture](architecture.md#4-data-model).

## 6. Handle errors

xat-rag never retries. Your app decides if it sends the request again.

- `409 index_mismatch_error`: the index of the RAG needs a rebuild. Rebuild the RAG and send its documents again.
- `503 overloaded_error`: wait for the `Retry-After` time, then send the request again.
- `504 stage_timeout_error`: a stage took too long. The message names the stage.
- `400 invalid_request_error`: a field is not valid. The message names the field.

## 7. Change the LLM

Changing the LLM of the app needs no rebuild and no change to the xat-rag recipes. Count the tokens with the new tokenizer, and keep the same steps in this guide.
