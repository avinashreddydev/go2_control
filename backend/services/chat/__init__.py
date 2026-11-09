"""
System 2: the conversational brain.

The model chats with the operator in character. When the conversation calls
for motion it calls a tool: ``navigate`` hands one instruction to System 1
(the vision-action loop in ``backend.services.vla``), ``move`` drives a short
blind sequence, ``stop`` halts everything. System 1's outcome comes back to
this model as a tool result or a follow-up report.
"""
