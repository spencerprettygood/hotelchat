# HotelChat Production Deployment Procedure

This document outlines the step-by-step process for deploying the HotelChat application to Render.

## Pre-Deployment Preparation

1. **Complete the deployment checklist**
   - Review and complete all items in `deployment_checklist.md`
   - Ensure all environment variables are ready to be configured in Render

2. **Choose deployment time window**
   - Schedule deployment during low traffic periods
   - Inform team members about the maintenance window
   - Default recommendation: Deploy between 10pm-12am local time

3. **Backup existing production data**
   - Take a snapshot of the production database
   - Record current environment configuration
   - Note: Render automatically creates database backups, but an additional manual backup is recommended

## Staging Deployment & Testing

1. **Create staging environment on Render**
   - Create a new Render Web Service with staging name suffix
   - Use the same configuration as production but on a lower tier
   - Set `NODE_ENV` or equivalent to "staging"

2. **Deploy to staging**
   ```bash
   git push origin main
   # Then deploy the main branch to staging via Render dashboard
   ```

3. **Run staging verification tests**
   ```bash
   python staging_verification.py --url https://hotelchat-staging.onrender.com
   ```

4. **Manually verify key functionality**
   - Login to the admin dashboard
   - Test the chatbot interface
   - Verify WhatsApp integration if applicable
   - Test real-time message delivery

5. **Address any issues found**
   - Fix any bugs identified in staging
   - Re-deploy and re-test if necessary
   - Only proceed to production when all tests pass

## Production Deployment

1. **Final pre-deployment checks**
   - Review the Git commit to be deployed
   - Verify all migrations are ready
   - Confirm third-party service status (OpenAI, Twilio, etc.)

2. **Deploy to production**
   - Via Render Dashboard:
     - Navigate to the Web Service
     - Click "Manual Deploy" and select "Deploy latest commit"
     - Monitor the build logs for any errors

3. **Monitor deployment**
   - Watch the Render deployment logs
   - Monitor application logs during startup
   - Check for any initialization errors

4. **Post-deployment verification**
   ```bash
   python production_verification.py --url https://hotelchat.onrender.com
   ```

5. **Manual verification**
   - Login to the production dashboard
   - Verify live conversations are appearing
   - Send test messages through each channel

## Rollback Procedure

If critical issues are discovered after deployment, follow these steps to rollback:

1. **Immediate assessment**
   - Determine severity of the issue
   - Decide if rollback is necessary or if a hotfix is preferable

2. **Rollback via Render Dashboard**
   - Navigate to the Web Service "Deploys" tab
   - Find the last stable deployment
   - Click "Rollback to this deploy"
   - Monitor logs during rollback

3. **Verify rollback success**
   - Run verification script against production
   - Manually check that critical functionality is restored

4. **Communication**
   - Inform team about the rollback
   - Document the issues encountered
   - Create tickets for fixing the issues before next deployment attempt

## Post-Deployment Tasks

1. **Monitor application for 24 hours**
   - Check error logs periodically
   - Monitor system resource usage
   - Watch for any unusual behavior

2. **Document deployment results**
   - Record any issues encountered
   - Document any manual steps that were required
   - Update deployment procedure if needed

3. **Clean up staging environment**
   - Consider scaling down or suspending staging to save costs
   - Preserve logs for future reference

## Emergency Contacts

- **Technical Lead**: [Name] - [Phone Number]
- **Render Support**: support@render.com
- **OpenAI Status**: https://status.openai.com/
- **Twilio Status**: https://status.twilio.com/
