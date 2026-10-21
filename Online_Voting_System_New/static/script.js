function showAuth(panelId, button) {
    document.querySelectorAll('.auth-panel').forEach(panel => panel.classList.remove('active-panel'));
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById(panelId).classList.add('active-panel');
    button.classList.add('active');
}

function validateSignup() {
    const password = document.getElementById('signup-password').value;
    const confirmPassword = document.getElementById('confirm-password').value;
    if (password.length < 6) {
        alert('Password must contain at least 6 characters.');
        return false;
    }
    if (password !== confirmPassword) {
        alert('Passwords do not match.');
        return false;
    }
    return true;
}

function validateResetPassword() {
    const password = document.getElementById('new-password').value;
    const confirmPassword = document.getElementById('confirm-new-password').value;
    if (password.length < 6) {
        alert('New password must contain at least 6 characters.');
        return false;
    }
    if (password !== confirmPassword) {
        alert('New passwords do not match.');
        return false;
    }
    return true;
}

function confirmVote(candidateName) {
    return confirm('Are you sure you want to vote for ' + candidateName + '?');
}

function confirmDeleteCandidate(candidateName) {
    return confirm(
        'Delete ' + candidateName + '?\n\n' +
        'If this candidate already has votes, those linked votes will also be removed from the election results. Affected voters will remain marked as already voted and cannot vote again.'
    );
}

setTimeout(() => {
    document.querySelectorAll('.flash').forEach(el => el.remove());
}, 7000);

function copyVoterId(voterId) {
    navigator.clipboard.writeText(voterId).then(() => {
        alert('Voter ID copied: ' + voterId);
    }).catch(() => {
        alert('Your Voter ID is: ' + voterId);
    });
}


function confirmDeleteVoter(name) {
    return confirm("Remove voter " + name + " from the system? This action cannot be undone.");
}
