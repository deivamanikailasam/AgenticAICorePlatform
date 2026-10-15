# How to add a tool

1. **Write the tool** by implementing `agent_contracts.interfaces.Tool`:

   ```python
   class CreateTicketTool(Tool):
       @property
       def spec(self) -> ToolSpec:
           return ToolSpec(
               name="create_ticket",  # what the model sees: [a-zA-Z0-9_-]
               ref="local://helpdesk/create_ticket",  # where it comes from
               version="1.0.0",
               description="Create a helpdesk ticket.",
               input_schema={
                   "type": "object",
                   "properties": {"title": {"type": "string", "maxLength": 200}},
                   "required": ["title"],
                   "additionalProperties": False,
               },
               output_schema={
                   "type": "object",
                   "properties": {"id": {"type": "string"}},
                   "required": ["id"],
               },
               side_effect=SideEffect.WRITE,  # read | write | irreversible
               idempotent=False,
               timeout_seconds=10,
               owner="support-platform",
               data_classification=DataClass.INTERNAL,
           )

       async def invoke(self, arguments, ctx): ...
   ```

2. **Pick the side-effect class honestly.** Anything that cannot be undone (payments,
   deletions, emails to customers) is `irreversible`: it gets an approval gate (Step 4) and is
   never retried automatically (Step 6).
3. **Name the tenant when the tool touches tenant-owned data.** Take a `tenant_id` argument; the
   authorising executor passes it to policy as `resource_tenant_id`, and a call for another
   tenant's data is denied and audited (Step 10). Tag tools that reach people or systems outside
   the company with `tags=("external",)`: in medium-risk agents their writes need approval.
4. **Test it** with the shared contract: subclass `agent_contracts.testing.ToolContract` and set
   `example_arguments` on the tool.
5. **Register it**: an entry point `"tool.create_ticket" = "your_pkg.tools:CreateTicketTool"`.
6. **Regenerate cards**: `just cards` (and `just catalogue`).
7. **Declare it in a spec**: `tools: [{ref: local://helpdesk/create_ticket}]`.
