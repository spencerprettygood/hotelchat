# Deployment Checklist – HotelChat

## Phase 1: Preparation
- [x] requirements.txt pinned
- [x] `render.yaml` with web & worker services
- [x] Gunicorn / Celery start commands verified
- [x] Health-check path (`/`) configured
- [x] ENV variables created in Render dashboard
- [x] Database & Redis provisioned
- [x] Staging URL tested via `staging_verification.py`
- [x] Production verification script ready
- [x] Rollback procedure documented in `deployment_procedure.md`

## Phase 2: Environment Variables
Ensure these environment variables are set in your Render dashboard:

- [ ] **OPENAI_API_KEY** - Your OpenAI API key
- [ ] **DATABASE_URL** - PostgreSQL connection string
- [ ] **SECRET_KEY** - Secure random string for Flask sessions
- [ ] **REDIS_URL** - Redis connection string
- [ ] **TWILIO_ACCOUNT_SID** - Twilio account identifier
- [ ] **TWILIO_AUTH_TOKEN** - Twilio authentication token
- [ ] **TWILIO_WHATSAPP_NUMBER** - WhatsApp number with whatsapp: prefix
- [ ] **WHATSAPP_API_TOKEN** - Token for WhatsApp API (if used)
- [ ] **SERVER_URL** - Public URL of the application
- [ ] **GOOGLE_SERVICE_ACCOUNT_KEY** - Google Service Account credentials JSON
- [ ] **LOG_LEVEL** - Set to INFO for production
- [ ] **OPENAI_CONCURRENCY** - Set to 5 (or adjust based on plan limits)

## Phase 3: Database
- [ ] Verify PostgreSQL database is provisioned
- [ ] Check database connection works
- [ ] Database will be automatically initialized by application on first run

## Phase 4: Redis
- [ ] Verify Redis instance is provisioned
- [ ] Test Redis connection
- [ ] Check Redis persistence settings if needed

## Phase 5: Application Settings
- [ ] Set WEB_CONCURRENCY properly (default: 2 × CPU cores + 1)
- [ ] Set GUNICORN_LOG_LEVEL to info
- [ ] Configure health check endpoint to /

## Security
- [ ] Ensure all API keys are properly secured
- [ ] Review access controls for the admin dashboard
- [ ] Verify SSL/TLS is enforced on all connections
- [ ] Check session timeout settings

## Performance
- [ ] Review caching strategy
- [ ] Set appropriate Celery worker count
- [ ] Configure appropriate timeouts for external API calls

## Testing
- [ ] Run OpenAI diagnostic tool before deployment
- [ ] Test Socket.IO functionality
- [ ] Verify WhatsApp integration
- [ ] Check Redis and database connections

## Monitoring
- [ ] Set up logging to capture application errors
- [ ] Configure alerts for critical errors
- [ ] Enable performance monitoring

## Documentation
- [ ] Update API documentation if applicable
- [ ] Document deployment architecture
- [ ] Create troubleshooting guide for common issues
