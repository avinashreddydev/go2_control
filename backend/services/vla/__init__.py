"""
System 1: the vision-action loop that carries out one instruction.

System 2 is the chat model. When a conversation calls for motion it calls the
``navigate`` tool with an instruction and a visible completion condition;
``executor.VLARunner`` then loops ``system1.act`` (frame in, small F/B/L/R
burst out) until System 1 reports the condition met, a limit is hit, or the
operator stops it. The outcome goes back to System 2 as the tool result.

Submodules are imported directly (``backend.services.vla.executor``) so that
importing the action grammar does not pull in OpenCV or the OpenAI client.
"""
