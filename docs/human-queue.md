# The human queue

Some accounts cannot be settled by reading their website. The human queue is where the agent parks them with one question, so a person answers the question and the agent does not guess.

## When an account goes in

- The qualify step ends in the **unclear** arm.
- A fact the draft depends on has no source, and cutting it would remove the reason to write.
- The pre-send check finds a block that a person might want to overrule on purpose.

## The format

A task on the company, with the question as its subject:

```
HUMAN QUEUE: {Company}: {the one question that settles it}
```

Plus a `HUMAN QUEUE` note on the company holding what was found so far, with URLs.

One question per task. If two questions are open, the account is not ready for the queue. It needs more research.

## Who clears it and how

The person who owns the lane reads the queue. For each task they do one of three things:

| Answer | What happens |
|---|---|
| The question is answered | They write the answer as a note with its source. The account goes back to qualify |
| The account is not worth it | They write a `DQ` note with the reason and set the status |
| It needs to wait | They leave the task open and date it |

The task is marked complete when it is answered. It is never deleted, so the next run can see the question was asked.

## What does not belong in the queue

- A draft that failed the gate. Fix the draft.
- A missing contact. That is a research task for the qualify step.
- Anything the agent could answer by reading one more page.
