"""Example: multi-tab workflow — sign up on a site, check email for verification."""
from fantoma import Agent
from fantoma.browser.verification import extract_verification_code

agent = Agent(
    llm_url="http://localhost:8080/v1",  # Any OpenAI-compatible endpoint
)

with agent.session("https://example.com/register") as s:
    # Tab 0: Fill signup form
    s.act("Type 'user@email.com' in the email field")
    s.act("Type 'MyPassword123' in the password field")
    s.act("Click Sign Up")

    # Tab 1: Open email to get verification code
    s.new_tab("https://mail.example.com", name="email")
    s.act("Log in and open the verification email")

    # Extract the code with regex — no LLM call needed
    code = extract_verification_code(s._browser.get_page())
    print(f"Verification code: {code}")

    # Switch back to signup tab and enter the code
    s.switch_tab("main")
    s.act(f"Type '{code}' in the verification field")
    s.act("Click Verify")

    # Check what tabs are open
    print(f"Open tabs: {s.tabs}")

    # Clean up the email tab
    s.close_tab("email")
