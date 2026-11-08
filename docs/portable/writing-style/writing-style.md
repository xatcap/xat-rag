# Writing style

All text in this repository follows the writing rules of ASD-STE100 (Simplified Technical English). These rules apply to documentation, code comments, API descriptions, error messages and log messages.

The aim is text that a person can read and understand quickly. Many readers do not have English as a first language.

This project does not check text against the ASD-STE100 approved-word dictionary. Thus, the text follows the style of the standard, but it is not certified as compliant.

## Rules

1. Write procedures in sentences of 20 words or fewer. Write descriptions in sentences of 25 words or fewer.
2. Write paragraphs of 6 sentences or fewer.
3. Write one topic in each sentence.
4. Write one instruction in each step. Use the imperative: "Set the key", not "The key should be set".
5. Use the active voice.
6. Use the present, simple past or simple future tense.
7. Use one word for one meaning, and one meaning for one word.
8. Use simple verbs and nouns in place of `-ing` forms where possible.
9. Do not use contractions, idioms or informal phrases.
10. Keep the articles "the", "a" and "an".
11. Do not make a group of more than 3 nouns. Rewrite the group, or use a hyphen.
12. Use a vertical list for 3 or more items.
13. Put a warning before the step that it applies to.
14. Do not change technical names, such as identifiers, flags, environment variables, products and field names.
15. Never use m-dashes.

If a rule makes a sentence harder to read, make the sentence easy to read. For example, write "check" and not "do a check of".

## Terms

| Use | Meaning | Do not use |
|---|---|---|
| provider | A configured source of models, such as `anthropic` or `local`. | backend |
| vendor | The company that operates a cloud service. | provider, for the company |
| start | Make a program or a task begin. | launch, spin up, boot |
| stop | Make a program or a task end. | shut down, terminate. Use "kill" only for the operating system action. |
| caller | A program that sends requests to the engine. | client, for the program that uses the API |
