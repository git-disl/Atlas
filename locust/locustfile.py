from locust import LoadTestShape, HttpUser, task, between, events
import numpy as np
import resource
import pickle
import random
import math
import os
resource.setrlimit(resource.RLIMIT_NOFILE, (250000, 250000))


########################################################################################################################
# Simulation Configuration
########################################################################################################################
GLOBAL_NGINX_FRONTEND_URL  = 'CHANGE_THIS_URL'
GLOBAL_MEDIA_FRONTEND_URL  = 'CHANGE_THIS_URL'

LOW_DAYS = 3
HIGH_DAYS = 7
GLOBAL_EXPERIMENT_DURATION = 60 * 10 * (LOW_DAYS + HIGH_DAYS)
GLOBAL_SECONDS_PER_DAY     = 60 * 10
GLOBAL_MIN_USERS_I         = 100
GLOBAL_MIN_USERS_II        = 300
GLOBAL_PEAKS_I             = [260, 280, 300]
GLOBAL_PEAKS_II            = [780, 840, 900]
GLOBAL_RANDOMNESS          = 0.10
GLOBAL_WAIT_TIME           = between(1, 3)


########################################################################################################################
texts = [text.replace('@', '') for text in list(open('datasets/fb-posts/news.txt'))]
media = [os.path.join('datasets/inria-person', fname) for fname in os.listdir('datasets/inria-person')]
users = list(range(1, 963))
users_dummy_free = list(range(1000, 100000))
users_dummy_used = []
friendship = set()

cycle = 0
active_users, inactive_users = [], list(range(1, 963))
with open('datasets/social-graph/socfb-Reed98.mtx', 'r') as f:
    friends = {}
    for edge in f.readlines():
        edge = list(map(int, edge.strip().split()))
        if len(edge) == 0:
            continue
        if edge[0] not in friends:
            friends[edge[0]] = set()
        if edge[1] not in friends:
            friends[edge[1]] = set()
        friends[edge[0]].add(edge[1])
        friends[edge[1]].add(edge[0])
    friends = {user: list(l) for user, l in friends.items()}


########################################################################################################################
class LoadShape(LoadTestShape):
    peak_one_users = None
    peak_two_users = None
    second_of_day = None

    def tick(self):
        global cycle
        if GLOBAL_EXPERIMENT_DURATION is not None and round(self.get_run_time()) > GLOBAL_EXPERIMENT_DURATION:
            return None

        second_of_day = round(self.get_run_time()) % GLOBAL_SECONDS_PER_DAY
        if self.second_of_day is None or second_of_day < self.second_of_day:
            cycle += 1
            global_peak = GLOBAL_PEAKS_I if cycle <= LOW_DAYS else GLOBAL_PEAKS_II
            self.peak_one_users = random.choice(global_peak)
            self.peak_two_users = random.choice(global_peak)
        self.second_of_day = second_of_day
        min_users = GLOBAL_MIN_USERS_I if cycle <= LOW_DAYS else GLOBAL_MIN_USERS_II
        user_count = (
                (self.peak_one_users - min_users)
                * math.e ** -(((second_of_day / (GLOBAL_SECONDS_PER_DAY / 10 * 2 / 3)) - 5) ** 2)
                + (self.peak_two_users - min_users)
                * math.e ** -(((second_of_day / (GLOBAL_SECONDS_PER_DAY / 10 * 2 / 3)) - 10) ** 2)
                + min_users
        )
        max_offset = math.ceil(user_count * GLOBAL_RANDOMNESS)
        user_count += random.choice(list(range(-max_offset, max_offset + 1)))
        return round(user_count), round(min(user_count, 70))


class SocialNetworkUser(HttpUser):
    wait_time = GLOBAL_WAIT_TIME
    host = GLOBAL_NGINX_FRONTEND_URL
    local_cycle = cycle

    def check_cycle(self):
        if cycle == self.local_cycle:
            return

        if cycle <= LOW_DAYS:
            compositions = self.compositions1
        else:
            compositions = self.compositions2

        tasks = []
        for func, weight in compositions.items():
            tasks += [func] * weight
        self.tasks = tasks
        self.local_cycle = cycle

    @task
    def login(self):
        self.check_cycle()
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        user_id = random.choice(users)
        data = {'username': 'username_%d' % user_id,
                'password': 'password_%d' % user_id}

        self.client.post("/wrk2-api/user/login", data=data, headers=headers)

    @task
    def register(self):
        self.check_cycle()
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        user = users_dummy_free.pop(0)
        users_dummy_used.append(user)
        data = {'first_name': 'first_name_%d' % user,
                'last_name': 'last_name_%d' % user,
                'username': 'username_%d' % user,
                'password': 'password_%d' % user,
                'user_id': user}

        self.client.post("/wrk2-api/user/register", data=data, headers=headers)

    @task
    def follow(self):
        self.check_cycle()
        if len(users_dummy_used) < 10:
            return
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        link = tuple(random.sample(users_dummy_used, 2))
        while link in friendship:
            link = tuple(random.sample(users_dummy_used, 2))
        friendship.add(link)
        user1, user2 = link
        self.client.post("/wrk2-api/user/follow", data={'user_id': user1, 'followee_id': user2}, headers=headers)

    @task
    def unfollow(self):
        self.check_cycle()
        if len(friendship) < 2:
            return
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        link = random.choice(tuple(friendship))
        friendship.remove(link)
        user1, user2 = link
        self.client.post("/wrk2-api/user/unfollow", data={'user_id': user1, 'followee_id': user2}, headers=headers)

    @task
    def readHomeTimeline(self):
        self.check_cycle()
        start = random.randint(0, 100)
        stop = start + 10

        response = self.client.get(
            "/wrk2-api/home-timeline/read?start=%s&stop=%s&user_id=%s" % (str(start), str(stop), str(self.user_id)),
            name="/wrk2-api/home-timeline/read")
        for post in eval(response.content):
            if len(post['media']) > 0:
                fname = post['media'][0]['media_id'] + '.' + post['media'][0]['media_type']
                self.client.get(
                    '%s/get-media?filename=%s' % (GLOBAL_MEDIA_FRONTEND_URL, fname),
                    name='/get-media')

    @task
    def readUserTimeline(self):
        self.check_cycle()
        start = random.randint(0, 100)
        stop = start + 10
        user_id = random.choice(friends[self.user_id])

        response = self.client.get(
            "/wrk2-api/user-timeline/read?start=%s&stop=%s&user_id=%s" % (str(start), str(stop), str(user_id)),
            name='/wrk2-api/user-timeline/read')
        for post in eval(response.content):
            if len(post['media']) > 0:
                fname = post['media'][0]['media_id'] + '.' + post['media'][0]['media_type']
                self.client.get(
                    '%s/get-media?filename=%s' % (GLOBAL_MEDIA_FRONTEND_URL, fname),
                    name='/get-media')

    @task
    def composePost(self):
        self.check_cycle()
        text = random.choice(texts)

        # User mentions
        number_of_user_mentions = random.randint(0, min(5, len(friends[self.user_id])))
        if number_of_user_mentions > 0:
            for friend_id in random.choices(friends[self.user_id], k=number_of_user_mentions):
                text += " @username_" + str(friend_id)
        # Media
        media_id = ''
        media_type = ''
        if random.random() < 0.20:
            with open(random.choice(media), "rb") as f:
                media_response = self.client.post('%s/upload-media' % GLOBAL_MEDIA_FRONTEND_URL,
                                                  files={"media": f})
            if media_response.ok:
                media_json = eval(media_response.text)
                media_id = '"%s"' % media_json['media_id']
                media_type = '"%s"' % media_json['media_type']
        # URLs - Note: no need to add it as the original post content has URLs already

        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        data = {'username': 'username_' + str(self.user_id),
                'user_id': str(self.user_id),
                'text': text,
                'media_ids': "[" + str(media_id) + "]",
                'media_types': "[" + str(media_type) + "]",
                'post_type': '0'}

        self.client.post("/wrk2-api/post/compose", data=data, headers=headers)

    def on_stop(self):
        active_users.remove(self.user_id)
        inactive_users.append(self.user_id)

    def on_start(self):
        self.user_id = random.choice(inactive_users)
        active_users.append(self.user_id)
        inactive_users.remove(self.user_id)

    compositions1 = {login: 8, register: 8, follow: 8, unfollow: 2,
                     readHomeTimeline: 50, readUserTimeline: 10, composePost: 15}
    compositions2 = {login: 8, register: 8, follow: 8, unfollow: 2,
                     readHomeTimeline: 50, readUserTimeline: 10, composePost: 215}