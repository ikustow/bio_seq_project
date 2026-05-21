# One-minute presentation: Assistant Think Mode

## 60-second talk track

Think Mode adds suggested next questions to the normal chat. The user asks a question, the assistant answers, and then a separate flow prepares three prompts for what to ask next.

First, it uses context tools to read only context that is already available: the latest user question, the latest assistant answer, recent chat history, the selected protein card, and possible next topics. This matters because Think Mode does not start or promise a new vector search.

Then the AI Agent gets a focused task: create exactly three short follow-up prompts. We check the format, check for duplicates, and show the prompts in chat as clickable chips below the assistant answer.

The user can click any chip to continue the conversation on that topic. This makes the chat feel more guided and interactive, and helps the user explore related ideas without writing the next question from scratch.
