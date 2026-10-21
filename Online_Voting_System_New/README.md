ONLINE VOTING SYSTEM - FINAL VERSION

Features:
- Voter Login, Sign Up, Admin Login on one page using tabs.
- Unique alphanumeric Voter ID such as VT482913.
- Mobile number is required during voter signup and stored uniquely.
- After signup, the Voter ID is shown in the web notification and can be sent by SMS when Twilio is configured.
- Forgot Password page uses the registered mobile number and a 6-digit OTP.
- Password reset OTP expires after 10 minutes.
- For local academic testing without Twilio, the OTP is shown as a Demo OTP on the reset page.
- Admin can see voter name, Voter ID, email, and mobile number.
- Admin can remove registered voters who have not voted yet. Voters who already voted cannot be removed so recorded election results are not altered.
- Election results are visible only in the Admin Dashboard.
- Admin can set voting start/end time and manage candidates.
- Each voter can vote only once.

SETUP
1. Run database.sql in MySQL/SQLTools.
   - For an existing database from an earlier version, app.py automatically adds the missing `phone` column.
2. Open app.py and replace YOUR_MYSQL_PASSWORD with your MySQL root password.
3. In VS Code terminal:
   python -m venv venv
   venv\\Scripts\\activate
   pip install -r requirements.txt
   python app.py
4. Open http://127.0.0.1:5000

DEMO ADMIN
Admin ID: ADMIN001
Password: admin123

REAL SMS SETUP (OPTIONAL)
The project uses Twilio for SMS. Create a Twilio account and obtain:
- Account SID
- Auth Token
- Twilio phone number capable of sending SMS

Set these environment variables before starting Flask (PowerShell example):
$env:TWILIO_ACCOUNT_SID="your_sid"
$env:TWILIO_AUTH_TOKEN="your_token"
$env:TWILIO_PHONE_NUMBER="+1xxxxxxxxxx"
python app.py

When Twilio is not configured, signup still works and the Voter ID is shown on screen. Forgot Password uses a demo OTP shown on the reset page so the project can be tested locally.

NOTE
This is an academic/demo application, not a production public-election system. Change the default secret key and demo admin password before any real deployment.


### Election rounds
The old version used `users.has_voted`, which permanently blocked a voter after their first vote. The updated version uses `votes.election_id` and a unique `(voter_id, election_id)` rule. This means the same voter can vote once in Election 1, once in Election 2, and so on.

To start the next election: wait until the current election is closed, then set a new Start Time and End Time in Admin Dashboard and click **Save Voting Time**. The app creates a new election round automatically; existing registered voters are eligible again for that new round.

### Admin candidate deletion
Candidate deletion is admin-only. An admin can delete a candidate whether or not votes exist for that candidate. If votes exist, those linked vote rows are deleted. This is the existing project behavior and means those historical vote rows are not retained after the candidate is deleted.
